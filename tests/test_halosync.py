# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5

from datetime import datetime

import pytest

pytest.importorskip("tkinter")
halosync = pytest.importorskip("haloviewer.halosync")


def test_extract_timestamp_both_notations():
    assert halosync.extract_timestamp("VAD_77_20221020_111520.hpl") == \
        datetime(2022, 10, 20, 11, 15, 20)
    assert halosync.extract_timestamp("Background_170926-065156.txt") == \
        datetime(2026, 9, 17, 6, 51, 56)
    assert halosync.extract_timestamp("system_parameters_77.txt") is None


def test_classify_subgroup():
    assert halosync.classify_subgroup(
        "Proc", "Processed_Wind_Profile_77_20260921_000812.hpl") == \
        "Processed_Wind_Profile"
    assert halosync.classify_subgroup(
        "Proc", "Wind_Profile_77_20260921_000812.hpl") == "Wind_Profile"
    assert halosync.classify_subgroup("Raw", "aet_VAD_1.raw") == "VAD"
    assert halosync.classify_subgroup("Proc", "junk.txt") is None


def test_scan_and_transfer_list(tmp_path):
    src = tmp_path / "src"
    day = src / "Proc" / "2026" / "202609" / "20260921"
    day.mkdir(parents=True)
    for name in ["VAD_77_20260921_000000.hpl", "VAD_77_20260921_010000.hpl"]:
        (day / name).write_text("x")
    data, found = halosync.scan_root(src)
    assert found
    proc = data["Proc"]
    assert len(proc.subgroups["VAD"].files) == 2
    # newest file is the "current file" and is excluded by default
    assert proc.current_file.endswith("VAD_77_20260921_010000.hpl")
    dst = tmp_path / "dst"
    dst.mkdir()
    todo = halosync.build_transfer_list(
        data, dst, {"Proc": "all"}, include_current=False, force_all=False)
    assert [f.relpath.rsplit("/", 1)[1] for f in todo] == \
        ["VAD_77_20260921_000000.hpl"]
    assert halosync.subgroup_light(proc, dst, "VAD") == "orange"


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

import gc  # noqa: E402
import smtplib  # noqa: E402
import socketserver  # noqa: E402
import threading  # noqa: E402


def _smtp_cfg(**kw):
    base = dict(via_email=True, email_mode="smtp", smtp_host="127.0.0.1",
                smtp_port=25, smtp_security="none", smtp_user="",
                smtp_sender="lidar@example.org", recipients="ops@example.org")
    base.update(kw)
    return halosync.AlertConfig(**base)


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: records the commands and the message
    data; refuses recipients starting with 'bad'."""

    def _send(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        log = self.server.log
        self._send("220 test ESMTP")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            cmd = line.split(" ", 1)[0].upper()
            log.append(line)
            if cmd in ("EHLO", "HELO"):
                self._send("250 test")
            elif cmd == "MAIL":
                self._send("250 ok")
            elif cmd == "RCPT":
                self._send("550 no such user" if "<bad" in line else "250 ok")
            elif cmd == "RSET":
                self._send("250 ok")
            elif cmd == "DATA":
                self._send("354 go ahead")
                data = []
                while True:
                    d = self.rfile.readline().decode()
                    if d in (".\r\n", ".\n", ""):
                        break
                    data.append(d)
                self.server.messages.append("".join(data))
                self._send("250 queued")
            elif cmd == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("502 not implemented")


@pytest.fixture
def smtp_server():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
    srv.daemon_threads = True
    srv.log, srv.messages = [], []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_sync_is_faulty_thresholds():
    cfg = halosync.AlertConfig()  # defaults: 2 files, 1 kB
    assert halosync.sync_is_faulty(cfg, 0, 0)
    assert halosync.sync_is_faulty(cfg, 1, 10_000)      # too few files
    assert halosync.sync_is_faulty(cfg, 5, 1023)        # too little data
    assert not halosync.sync_is_faulty(cfg, 2, 1024)
    cfg.min_files = cfg.min_kb = 0                      # both thresholds off
    assert not halosync.sync_is_faulty(cfg, 0, 0)


def _day(d, h=12):
    return datetime(2026, 11, d, h, 0, 0)


def test_alert_once_and_recovery():
    cfg = halosync.AlertConfig(canary=True)              # alert_once, daily canary
    assert cfg.alert_once and cfg.canary_daily
    st = halosync.AlertState()
    kind, st = halosync.next_message(cfg, st, True, _day(1, 10))
    assert kind == "alert" and st.faulty_since == "2026-11-01T10:00:00"
    # sending failed -> not marked, so the next faulty sync tries again
    kind, st = halosync.next_message(cfg, st, True, _day(1, 11))
    assert kind == "alert" and st.faulty_since == "2026-11-01T10:00:00"
    st = halosync.mark_sent(st, kind, _day(1, 11))
    assert st.alert_sent
    # still faulty: nothing more
    kind, st = halosync.next_message(cfg, st, True, _day(1, 12))
    assert kind is None
    # works again: recovery message, state cleared
    kind, st = halosync.next_message(cfg, st, False, _day(1, 13))
    assert kind == "recovered" and st.faulty_since == "" and not st.alert_sent
    st = halosync.mark_sent(st, kind, _day(1, 13))
    # recovery counts as the day's canary
    kind, st = halosync.next_message(cfg, st, False, _day(1, 14))
    assert kind is None
    kind, st = halosync.next_message(cfg, st, False, _day(2, 0))
    assert kind == "canary"


def test_alert_every_time_and_canary_every_sync():
    cfg = halosync.AlertConfig(canary=True, canary_daily=False, alert_once=False)
    st = halosync.AlertState()
    for h in (1, 2, 3):
        kind, st = halosync.next_message(cfg, st, True, _day(1, h))
        assert kind == "alert"
        st = halosync.mark_sent(st, kind, _day(1, h))
    kind, st = halosync.next_message(cfg, st, False, _day(1, 4))
    assert kind == "recovered"
    st = halosync.mark_sent(st, kind, _day(1, 4))
    for h in (5, 6):
        kind, st = halosync.next_message(cfg, st, False, _day(1, h))
        assert kind == "canary"


def test_no_recovery_message_if_no_alert_went_out():
    cfg = halosync.AlertConfig()                         # no canary
    kind, st = halosync.next_message(cfg, halosync.AlertState(), True, _day(1))
    assert kind == "alert"                               # ... but never sent
    kind, st = halosync.next_message(cfg, st, False, _day(1, 13))
    assert kind is None and st.faulty_since == ""


def test_alerts_configured_and_missing():
    cfg = _smtp_cfg()
    assert halosync.email_missing(cfg) == []
    assert halosync.alerts_configured(cfg)
    assert not halosync.alerts_configured(_smtp_cfg(via_email=False))
    assert not halosync.alerts_configured(
        _smtp_cfg(min_files=0, min_kb=0, canary=False))      # nothing to send
    assert "password" in halosync.email_missing(_smtp_cfg(smtp_user="u"))
    # sender falls back to the login
    assert halosync.email_missing(_smtp_cfg(smtp_sender="", smtp_user="u@x.org",
                                            smtp_password="p")) == []
    g = halosync.AlertConfig(via_email=True, email_mode="gmail",
                             gmail_address="me@gmail.com", recipients="a@b.c")
    assert halosync.email_missing(g) == ["app password"]
    g.gmail_password = "abcd efgh ijkl mnop"
    s = halosync.email_settings(g)
    assert (s["host"], s["port"], s["security"], s["user"], s["sender"]) == \
        ("smtp.gmail.com", 587, "STARTTLS", "me@gmail.com", "me@gmail.com")
    assert halosync.parse_recipients("a@b.c; d@e.f,g@h.i  j@k.l") == \
        ["a@b.c", "d@e.f", "g@h.i", "j@k.l"]


def test_alert_config_roundtrip_hides_passwords(tmp_path):
    path = tmp_path / "alerts.json"
    cfg = _smtp_cfg(enabled=True, smtp_user="u", smtp_password="s3cr3t-pw",
                    gmail_password="gmail-pw", min_files=7, canary=True)
    halosync.save_alert_config(path, cfg)
    text = path.read_text()
    assert "s3cr3t-pw" not in text and "gmail-pw" not in text
    assert halosync.load_alert_config(path) == cfg


def test_alert_state_is_remembered(tmp_path):
    path = tmp_path / "alerts.json"
    st = halosync.AlertState(faulty_since="2026-11-01T10:00:00", alert_sent=True,
                             last_canary="2026-10-31")
    halosync.save_alert_config(path, _smtp_cfg(), st)
    assert halosync.load_alert_state(path) == st
    # saving the settings alone keeps the state
    halosync.save_alert_config(path, _smtp_cfg(min_files=9))
    assert halosync.load_alert_state(path) == st
    assert halosync.load_alert_config(path).min_files == 9
    path.write_text('{"version": 1, "state": {"alert_sent": "yes", '
                    '"faulty_since": 3, "last_canary": "2026-11-02"}}')
    assert halosync.load_alert_state(path) == \
        halosync.AlertState(last_canary="2026-11-02")
    assert halosync.load_alert_state(tmp_path / "missing.json") == \
        halosync.AlertState()


def test_load_alert_config_tolerates_bad_files(tmp_path):
    path = tmp_path / "alerts.json"
    assert halosync.load_alert_config(path) == halosync.AlertConfig()
    path.write_text("{not json")
    assert halosync.load_alert_config(path) == halosync.AlertConfig()
    path.write_text('{"version": 1, "min_files": "x", "min_kb": -3, '
                    '"canary": 1, "email_mode": "pigeon", "smtp_host": "h", '
                    '"smtp_password": "garbage"}')
    cfg = halosync.load_alert_config(path)
    assert (cfg.min_files, cfg.min_kb, cfg.canary, cfg.email_mode,
            cfg.smtp_host, cfg.smtp_password) == (2, 1, False, "smtp", "h", "")


def test_build_alert_message():
    cfg = _smtp_cfg(recipients="a@b.c, d@e.f")
    msg = halosync.build_alert_message(cfg, "alert", 1, 512, source="S:/lidar",
                                       dest="D:/backup", note="no new files",
                                       errors=["x.hpl: denied"])
    assert "ALERT" in msg["Subject"] and "512 B in 1 file " in msg["Subject"]
    assert msg["To"] == "a@b.c, d@e.f" and msg["From"] == "lidar@example.org"
    body = msg.get_content()
    for part in ("S:/lidar", "D:/backup", "no new files", "x.hpl: denied",
                 "fewer than 2 files or less than 1 kB"):
        assert part in body
    assert "No further alerts" in body                   # alert_once is on
    canary = halosync.build_alert_message(cfg, "canary", 12, 3 * 1024 * 1024)
    assert "OK" in canary["Subject"] and "3.0 MB in 12 files" in canary["Subject"]
    rec = halosync.build_alert_message(cfg, "recovered", 4, 9000,
                                       faulty_since="2026-11-01T10:00:00")
    assert "RECOVERED" in rec["Subject"]
    assert "faulty since 01.11.2026 10:00" in rec.get_content()


def test_send_test_mail(smtp_server):
    port = smtp_server.server_address[1]
    cfg = _smtp_cfg(smtp_port=port, recipients="ops@example.org, boss@example.org")
    text = halosync.smtp_send_test(cfg, timeout=5)
    assert text.startswith("test mail sent to ops@example.org, boss@example.org")
    assert "REFUSED" not in text
    assert len(smtp_server.messages) == 1
    body = smtp_server.messages[0]
    assert "test message from" in body
    assert f"127.0.0.1:{port} (none)" in body and "lidar@example.org" in body
    assert "once per faulty spell" in body


def test_send_test_mail_some_recipients_refused(smtp_server):
    port = smtp_server.server_address[1]
    cfg = _smtp_cfg(smtp_port=port, recipients="ops@example.org, bad@example.org")
    text = halosync.smtp_send_test(cfg, timeout=5)
    assert "sent to ops@example.org " in text and "REFUSED: bad@example.org" in text
    assert len(smtp_server.messages) == 1


def test_send_test_mail_all_recipients_refused(smtp_server):
    port = smtp_server.server_address[1]
    cfg = _smtp_cfg(smtp_port=port, recipients="bad@example.org")
    with pytest.raises(smtplib.SMTPRecipientsRefused) as exc:
        halosync.smtp_send_test(cfg, timeout=5)
    assert halosync.describe_error(exc.value) == "recipient refused: bad@example.org"
    assert smtp_server.messages == []


def test_send_test_mail_incomplete():
    with pytest.raises(ValueError, match="SMTP server"):
        halosync.smtp_send_test(_smtp_cfg(smtp_host=""))


def test_send_email(smtp_server):
    port = smtp_server.server_address[1]
    cfg = _smtp_cfg(smtp_port=port)
    msg = halosync.build_alert_message(cfg, "alert", 0, 0, note="source gone")
    halosync.send_email(cfg, msg, timeout=5)
    assert len(smtp_server.messages) == 1
    assert "source gone" in smtp_server.messages[0]


def test_starttls_and_login_are_used(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(("connect", host, port))

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context=None):
            calls.append(("starttls",))

        def login(self, user, pw):
            calls.append(("login", user, pw))

        def send_message(self, msg, from_addr=None, to_addrs=None):
            calls.append(("send", from_addr, tuple(to_addrs)))
            return {}

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr(halosync.smtplib, "SMTP", FakeSMTP)
    cfg = halosync.AlertConfig(via_email=True, email_mode="gmail",
                               gmail_address="me@gmail.com",
                               gmail_password="app-pw", recipients="a@b.c")
    halosync.send_email(cfg, halosync.build_alert_message(cfg, "test", 0, 0))
    assert calls == [("connect", "smtp.gmail.com", 587), ("ehlo",), ("starttls",),
                     ("ehlo",), ("login", "me@gmail.com", "app-pw"),
                     ("send", "me@gmail.com", ("a@b.c",)), ("quit",)]


# -- GUI wiring -------------------------------------------------------------

@pytest.fixture
def app():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    a = halosync.LidarSyncApp(root)
    yield a
    root.destroy()
    del a, root
    # Collect Tk variables/images now, in the main thread: if a later
    # garbage collection runs in a worker thread, Tkinter complains
    # ("main thread is not in main loop") as an unraisable exception.
    gc.collect()


def test_enable_alerts_greyed_out_until_schedule_and_setup(app):
    assert str(app.alerts_chk.cget("state")) == "disabled"
    app.sync_schedule_var.set(True)
    app._on_schedule_toggle()
    assert str(app.alerts_chk.cget("state")) == "disabled"   # not configured
    app._alert_setup_saved(_smtp_cfg())
    assert str(app.alerts_chk.cget("state")) == "normal"
    app.sync_schedule_var.set(False)
    app._on_schedule_toggle()
    assert str(app.alerts_chk.cget("state")) == "disabled"
    # settings were saved to alerts.json
    saved = halosync.load_alert_config(halosync.get_alerts_path())
    assert saved.smtp_host == "127.0.0.1"


def test_scheduled_sync_without_data_sends_alert(app, monkeypatch):
    sent = []
    monkeypatch.setattr(halosync, "send_email", lambda cfg, msg: sent.append(msg))
    app._alert_setup_saved(_smtp_cfg())
    app.sync_schedule_var.set(True)
    app._on_schedule_toggle()
    app._perform_scheduled_sync()          # alerts not enabled yet
    app.alerts_var.set(True)
    app._on_alerts_toggle()
    app._perform_scheduled_sync()          # no source -> alert
    for _ in range(50):
        app.root.update()
        if sent and "sent" in app.alert_status_label.cget("text"):
            break
        app.root.after(20)
    assert len(sent) == 1
    assert "ALERT" in sent[0]["Subject"]
    assert "source not reachable" in sent[0].get_content()
    assert halosync.load_alert_config(halosync.get_alerts_path()).enabled


def test_alert_setup_dialog_collect(app):
    dlg = halosync.AlertSetupDialog(app.root, _smtp_cfg(smtp_port=2525),
                                    on_save=lambda c: None)
    try:
        dlg.min_files_var.set("5")
        dlg.mode_var.set("gmail")
        dlg._mode_changed()
        dlg.gmail_var.set("me@gmail.com")
        dlg.port_var.set("")                       # ignored in Gmail mode
        cfg = dlg.collect()
        assert (cfg.min_files, cfg.email_mode, cfg.gmail_address, cfg.smtp_port) == \
            (5, "gmail", "me@gmail.com", 587)
        dlg.mode_var.set("smtp")
        dlg.port_var.set("2525")
        dlg.security_var.set("SSL/TLS")            # non-standard port is kept
        dlg._security_changed()
        assert dlg.port_var.get() == "2525"
        dlg.port_var.set("587")
        dlg._last_security = "STARTTLS"
        dlg._security_changed()                    # standard port follows
        assert dlg.port_var.get() == "465"
    finally:
        dlg.destroy()


def _wait_until(app, cond, n=100):
    for _ in range(n):
        app.root.update()
        if cond():
            return True
        app.root.after(20)
    return False


def test_alert_sent_once_then_recovered_and_icon(app, monkeypatch):
    sent = []
    monkeypatch.setattr(halosync, "send_email", lambda cfg, msg: sent.append(msg))
    app._alert_setup_saved(_smtp_cfg())
    app.sync_schedule_var.set(True)
    app._on_schedule_toggle()
    assert app.alert_icon.icon_state == "off"          # not enabled yet
    app.alerts_var.set(True)
    app._on_alerts_toggle()
    assert app.alert_icon.icon_state == "ok"

    app._check_alerts(0, 0, note="lidar stopped")
    assert _wait_until(app, lambda: "sent" in app.alert_status_label.cget("text"))
    assert app.alert_icon.icon_state == "faulty"
    app._check_alerts(0, 0)                            # still faulty: no mail
    app.root.update()
    assert len(sent) == 1 and "already sent" in app.alert_status_label.cget("text")
    assert "FAULTY since" in app._alert_icon_text()
    # remembered across a restart of the program
    assert halosync.load_alert_state(halosync.get_alerts_path()).alert_sent

    app._check_alerts(5, 50_000)                       # works again
    assert _wait_until(app, lambda: len(sent) == 2
                       and "sent" in app.alert_status_label.cget("text"))
    assert "RECOVERED" in sent[1]["Subject"]
    assert app.alert_icon.icon_state == "ok"


def test_toggling_alerts_resets_faulty_state(app, monkeypatch):
    sent = []
    monkeypatch.setattr(halosync, "send_email", lambda cfg, msg: sent.append(msg))
    app._alert_setup_saved(_smtp_cfg())
    app.sync_schedule_var.set(True)
    app._on_schedule_toggle()
    app.alerts_var.set(True)
    app._on_alerts_toggle()
    app._check_alerts(0, 0)
    assert _wait_until(app, lambda: len(sent) == 1)
    app.root.update()
    app.alerts_var.set(False)
    app._on_alerts_toggle()
    assert app.alert_state.faulty_since == "" and app.alert_icon.icon_state == "off"
    app.alerts_var.set(True)
    app._on_alerts_toggle()
    app._check_alerts(0, 0)                            # alerts again after reset
    assert _wait_until(app, lambda: len(sent) == 2)


def test_failed_mail_shows_orange_and_retries(app, monkeypatch):
    def fail(cfg, msg):
        raise ConnectionRefusedError()
    monkeypatch.setattr(halosync, "send_email", fail)
    app._alert_setup_saved(_smtp_cfg(canary=True))
    app.sync_schedule_var.set(True)
    app._on_schedule_toggle()
    app.alerts_var.set(True)
    app._on_alerts_toggle()
    app._check_alerts(5, 50_000)                       # canary fails
    assert _wait_until(app, lambda: "NOT sent" in app.alert_status_label.cget("text"))
    assert app.alert_icon.icon_state == "mailfail"
    assert app.alert_state.last_canary == ""           # will be tried again
    app._check_alerts(0, 0)                            # faulty wins over mail failure
    assert _wait_until(app, lambda: app.alert_icon.icon_state == "faulty")
    assert not app.alert_state.alert_sent
