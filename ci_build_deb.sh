#!/bin/bash
# Abort on the first failing command. Without this, a failing dh_make
# (e.g. because the licence file was not found) went unnoticed and the
# script ran on into dpkg-buildpackage, which then died with the
# misleading "cannot open file debian/changelog".
set -eo pipefail

BUILD_VERSION=$1
if [ -z "$BUILD_VERSION" ]; then
  echo "ERROR: usage: $0 <sdist file name, e.g. pkg-1.2.3.tar.gz>" >&2
  exit 1
fi

FULLNAME=${BUILD_VERSION%.tar.gz}
VERSION=${FULLNAME##*-}

# Python (PEP 440) pre-release and dev versions must sort *before* the
# final release in Debian too. Debian only does that for "~", so turn
#   0.2.0rc2 -> 0.2.0~rc2,  0.2.0a1 -> 0.2.0~a1,  0.2.0b3 -> 0.2.0~b3,
#   0.2.1.dev3+g1a2b3c -> 0.2.1~dev3+g1a2b3c
# Only the public part (before "+") is touched, so letters inside a
# setuptools-scm local version such as "+g1a2b3c" stay unchanged.
PUBLIC_VERSION=${VERSION%%+*}
LOCAL_VERSION=${VERSION#"$PUBLIC_VERSION"}
PUBLIC_VERSION=$( echo "$PUBLIC_VERSION" \
  | sed -E -e 's/[.-]?(alpha|beta|rc|pre|preview|a|b|c)([0-9]*)/~\1\2/g' \
           -e 's/[.-]?dev([0-9]*)/~~dev\1/g' )
# ("~~dev": PEP 440 puts .devN before aN/bN/rcN; "~~" sorts before "~".)
DEB_VERSION="${PUBLIC_VERSION}${LOCAL_VERSION}"
echo "Python version: $VERSION -> Debian upstream version: $DEB_VERSION"
NAME=${FULLNAME%%-*}
CODENAME=$(cat /etc/os-release | grep VERSION_CODENAME | sed s/.*=// | tr -d '"')

# Function to extract metadata from pyproject.toml or _metadata.py
function get_project_info() {
    local FIELD=$1

    # First try to read from pyproject.toml using python
    python3 -c "
import sys
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        sys.exit(1)

with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
if '$FIELD' in ['author', 'email']:
    authors = data.get('project', {}).get('authors')
    if '$FIELD' == 'author':
      res = authors[0].get('name')
    else:
      res = authors[0].get('email')
elif '$FIELD' == 'description':
    res = data.get('project', {}).get('description')
else:
    res = 'Unknown'
print(res)
"
}

# Start from a clean, *existing* directory (previously an existing one
# was removed but not recreated, so the pushd below failed).
rm -rf deb_dist/$CODENAME
mkdir -p deb_dist/$CODENAME
pushd deb_dist/$CODENAME

cp ../../dist/${FULLNAME}.tar.gz .
tar -xzvf ${FULLNAME}.tar.gz
pushd ${FULLNAME}

# Get metadata from pyproject.toml
AUTHOR=$(get_project_info "author")
EMAIL=$(get_project_info "email")
DESCRIPTION=$(get_project_info "description")

# show what we got
echo "Using metadata: AUTHOR='$AUTHOR', EMAIL='$EMAIL', DESCRIPTION='$DESCRIPTION'"

# The sdist may ship the repository's debian/ directory (setuptools-scm
# puts every git-tracked file into it). dh_make refuses to run if
# debian/ exists, so move it aside and keep a hand-written copyright.
USER_COPYRIGHT=""
if [ -f debian/copyright ]; then
  USER_COPYRIGHT=$( mktemp )
  cp debian/copyright "$USER_COPYRIGHT"
fi
rm -rf debian/

# Licence file: accept the usual names instead of only LICENSE.txt.
# (With LICENSE.txt missing, readlink returned nothing, --copyrightfile
# swallowed the next option and dh_make failed without creating debian/.)
LICENSE_FILE=""
for F in LICENSE LICENSE.txt LICENSE.md LICENCE LICENCE.txt COPYING; do
  if [ -f "$F" ]; then LICENSE_FILE=$( readlink -e "$F" ); break; fi
done
if [ -z "$LICENSE_FILE" ]; then
  echo "ERROR: no licence file (LICENSE, LICENSE.txt, ...) found in sdist" >&2
  exit 1
fi
echo "Using licence file: $LICENSE_FILE"

export DEBFULLNAME="$AUTHOR"
dh_make --python -p ${NAME}_${DEB_VERSION}+1${CODENAME}1 \
  -f ../${FULLNAME}.tar.gz \
  -c custom \
  --copyrightfile "$LICENSE_FILE" \
  --email "$EMAIL" \
  --yes

# Prefer the maintained debian/copyright from the repository, if any.
if [ -n "$USER_COPYRIGHT" ]; then
  echo "Using debian/copyright from repository"
  cp "$USER_COPYRIGHT" debian/copyright
fi

ls -l debian
if [ ! -f debian/changelog ]; then
  echo "ERROR: dh_make did not create debian/changelog" >&2
  exit 1
fi

# Set the correct distribution *before* building, so the signed .changes
# file already has the right value. dh_make writes the top changelog
# entry with distribution "UNRELEASED"; patching debian/changelog here
# (a plain, unsigned source file) is safe -- unlike patching the .changes
# file *after* dpkg-buildpackage has signed it, which would invalidate
# the signature.
sed -i "0,/UNRELEASED/{s/UNRELEASED/${CODENAME}/}" debian/changelog

# Edit the control file - add description
echo " " >> debian/control
mv debian/control debian/control.old
awk '
BEGIN{tgt=0; dsc=0}
/^[[:space:]]*$/{if (tgt==1) {print "Description: '"$DESCRIPTION"'"}; tgt=0}
/^Package: python.*'$NAME'/{tgt=1}
/^Description: / && tgt==1 {dsc=1; next}
/^ [^[:space:]]/ && dsc==1 {next}
{print $0; dsc=0}
' debian/control.old | tee debian/control

# Remove doc package
echo " " >> debian/control
mv debian/control debian/control.old
awk '
BEGIN{doc=0}
/^Package: python.*'$NAME-doc'/{doc=1}
/^[[:space:]]*$/{doc=0}
(doc==0){print $0}
' debian/control.old | tee debian/control

# Add setuptools_scm, build and the PEP 517 pybuild plugin (needed for
# pyproject.toml-only packages without setup.py) to build dependencies
echo " " >> debian/control
mv debian/control debian/control.old
awk '
BEGIN{
  block=0
}
# one-line format
/^Build-Depends:\s*\S+/{
  # Check if setuptools-scm is already there
  if (index($0, "python3-setuptools-scm") == 0) {
    $0 = $0", python3-setuptools-scm"
  }
  if (index($0, "python3-build") == 0) {
    $0 = $0", python3-build"
  }
  if (index($0, "pybuild-plugin-pyproject") == 0) {
    $0 = $0", pybuild-plugin-pyproject"
  }
  print $0
  next
}
# newer multiline format
(block==1 && $0 ~ /python3-setuptools-scm/){
  pss=1
}
(block==1 && $0 ~ /python3-build/){
  pb=1
}
(block==1 && $0 ~ /pybuild-plugin-pyproject/){
  ppp=1
}
(block==1 && $0 ~ /^[^ ]/){
  block=0
  if (pss==0){
    print " python3-setuptools-scm,"
  }
  if (pb==0){
    print " python3-build,"
  }
  if (ppp==0){
    print " pybuild-plugin-pyproject,"
  }
}
/^Build-Depends:[\s]*/{
  block=1
  pss=0
  pb=0
  ppp=0
}
{print $0}
' debian/control.old | tee debian/control

# Handle Raspberry Pi architecture if needed
RASPBIAN_CODENAMES=("wheezy" "jessie" "stretch" "buster" "bullseye" "bookworm" "trixie" "forky")
if [[ $(echo "${RASPBIAN_CODENAMES[@]}" | fgrep -w $CODENAME) ]]; then
  #ARCH_OPTS="--host-arch armhf -d"
  cat << EOF > ~/tmp.sh
#!/bin/bash
sed -i 's/Build-Architecture: .*/Build-Architecture: armhf/' ../*.buildinfo
EOF
  chmod +x ~/tmp.sh
  ARCH_OPTS=--hook-changes=~/tmp.sh
fi

# Install every Build-Depends of the generated debian/control, so the
# build does not rely on the CI job having apt-installed the right list
# (dpkg-checkbuilddeps would otherwise abort on anything missing, e.g.
# pybuild-plugin-pyproject). Needs root, which the CI containers have.
if [ "$(id -u)" = "0" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || true
  apt-get -y --no-install-recommends build-dep ./
else
  echo "WARNING: not root, cannot install build dependencies" >&2
fi

# Disable tests during package build (they may need special setup)
export PYBUILD_DISABLE=test

# Install the signing key and get its ID
# $SIGNING_PRIVATE_KEY holds a PATH, not the key content
# "|| true": under set -e a failing import would otherwise abort here,
# before the explanatory error message below.
IMPORT_STATUS=$(gpg --batch --status-fd 1 --import "$SIGNING_PRIVATE_KEY" 2>/dev/null || true)
# NOTE: gpg emits a separate IMPORT_OK line for the public key half and
# the secret key half of the same import, both carrying the same
# fingerprint in field 4. Without "exit", awk matches both lines and
# concatenates them (newline-joined) into one garbled two-line string,
# which gpg/dpkg-buildpackage then fails to match to any real key
# ("No secret key"). Take just the first match.
SIGNING_PRIVATE_KEY_ID=$(echo "$IMPORT_STATUS" | awk '/IMPORT_OK/ {print $4; exit}')

if [ -z "$SIGNING_PRIVATE_KEY_ID" ]; then
  echo "ERROR: failed to import signing key or extract its ID" >&2
  gpg --batch --status-fd 1 --import "$SIGNING_PRIVATE_KEY" || true
  exit 1
fi

export DEB_SIGN_KEYID="$SIGNING_PRIVATE_KEY_ID"
dpkg-buildpackage $ARCH_OPTS -b
popd

# Optional: clean up source directory
# rm -rv $FULLNAME

popd
ls -l deb_dist/$CODENAME
if $( ls deb_dist/$CODENAME -h | grep '.changes' > /dev/null ); then
  echo "Debian packages built successfully."
else
  echo ".changes file not build, something is wrong!"
  exit 1
fi