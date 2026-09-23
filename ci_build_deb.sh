#!/bin/bash

BUILD_VERSION=$1

FULLNAME=${BUILD_VERSION%.tar.gz}
VERSION=${FULLNAME##*-}
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

if [ -e deb_dist/$CODENAME ]; then
  rm -r deb_dist/$CODENAME
else
  mkdir -p deb_dist/$CODENAME
fi
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

rm -r debian/ 2>/dev/null || true

export DEBFULLNAME="$AUTHOR"
dh_make --python -p ${NAME}_${VERSION}+1${CODENAME}1 \
  -f ../${FULLNAME}.tar.gz \
  -c custom \
  --copyrightfile $( readlink -e LICENSE.txt ) \
  --email "$EMAIL" \
  --yes

ls -l debian

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

# Add setuptools_scm to build dependencies
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
(block==1 && $0 ~ /^[^ ]/){
  block=0
  if (pss==0){
    print " python3-setuptools-scm,"
  }
  if (pb==0){
    print " python3-build,"
  }
}
/^Build-Depends:[\s]*/{
  block=1
  pss=0
  pb=0
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

# Disable tests during package build (they may need special setup)
export PYBUILD_DISABLE=test

# Install the signing key and get its ID
# $SIGNING_PRIVATE_KEY holds a PATH, not the key content
IMPORT_STATUS=$(gpg --batch --status-fd 1 --import "$SIGNING_PRIVATE_KEY" 2>/dev/null)
# NOTE: gpg emits a separate IMPORT_OK line for the public key half and
# the secret key half of the same import, both carrying the same
# fingerprint in field 4. Without "exit", awk matches both lines and
# concatenates them (newline-joined) into one garbled two-line string,
# which gpg/dpkg-buildpackage then fails to match to any real key
# ("No secret key"). Take just the first match.
SIGNING_PRIVATE_KEY_ID=$(echo "$IMPORT_STATUS" | awk '/IMPORT_OK/ {print $4; exit}')

if [ -z "$SIGNING_PRIVATE_KEY_ID" ]; then
  echo "ERROR: failed to import signing key or extract its ID" >&2
  gpg --batch --status-fd 1 --import "$SIGNING_PRIVATE_KEY"
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