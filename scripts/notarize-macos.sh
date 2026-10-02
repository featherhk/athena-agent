#!/bin/sh
# Credentials belong in the login Keychain, never command history or this repository.
set -eu
if [ "$#" -ne 2 ]; then
  echo "Usage: sh scripts/notarize-macos.sh /absolute/path/Athena.dmg keychain-profile" >&2
  exit 2
fi
image=$1
profile=$2
codesign --verify --strict "$image"
xcrun notarytool submit "$image" --keychain-profile "$profile" --wait
xcrun stapler staple "$image"
xcrun stapler validate "$image"
spctl --assess --type open --context context:primary-signature --verbose "$image"
shasum -a 256 "$image" > "$image.sha256"
echo "Notarization verified. Re-test a downloaded copy on a clean Mac before publishing."
