#!/usr/bin/env bash
# Build an iOS release IPA (or take a pre-built one) and upload it to TestFlight reliably.
# macOS only (needs Xcode: xcrun altool, lipo, plutil).
#
# Usage:
#   bash build_and_upload.sh <project-dir> [--no-clean]
#
# Inputs come from <project-dir>/ship.env (override the path with SHIP_ENV=/path/to/ship.env):
#   IOS_APP_ID     (required) App Store Connect numeric app ID (for the post-upload build check)
#   IOS_TEAM_ID    (required) Apple Developer Team ID used for signing
#   IOS_BUNDLE_ID  (optional) used only in log messages
#   ASC_KEY_ID     (optional) ASC API key ID; default: from `asc auth status`
#   ASC_ISSUER_ID  (optional) ASC issuer ID; default: from `asc auth issuer-id`
#   IOS_BUILD_CMD  (optional) command that produces a release IPA, run from <project-dir>.
#                  It may read $EXPORT_OPTIONS_PLIST: a disk-export ExportOptions plist this
#                  script generates (destination=export, not upload). Quote the value in
#                  ship.env with single quotes so $EXPORT_OPTIONS_PLIST is not expanded early.
#   IOS_IPA_PATH   (optional) path (or glob) of the IPA the build produces, relative to
#                  <project-dir> or absolute. Without IOS_BUILD_CMD it names a pre-built IPA:
#                  the build is skipped and that file is uploaded.
#   IOS_CLEAN_CMD  (optional) command run before building to clear build caches.
#   VERSION_NAME, BUILD_NUMBER  (optional) expected version / build; the script stops if the
#                  IPA's CFBundleShortVersionString / CFBundleVersion differ.
# With none of the IOS_*_CMD / IOS_IPA_PATH keys set, a project with pubspec.yaml uses the
# Flutter defaults; otherwise the script stops and asks for them (see docs/build-recipes.md).
#
# Flags:
#   --no-clean   skip the clean step (only if you KNOW no simulator build polluted the cache)
#
# Exits non-zero on any failure (missing config, bad arch, build error, upload error).
set -euo pipefail

PROJECT="${1:?usage: build_and_upload.sh <project-dir> [--no-clean]}"
PROJECT="$(cd "$PROJECT" && pwd)"
DO_CLEAN=1
[[ "${2:-}" == "--no-clean" ]] && DO_CLEAN=0

SHIP_ENV="${SHIP_ENV:-$PROJECT/ship.env}"
[[ -f "$SHIP_ENV" ]] || { echo "!! ship.env not found at $SHIP_ENV (copy ship.env.example to your app root and fill it in)"; exit 1; }
# Export everything ship.env defines so IOS_BUILD_CMD / IOS_CLEAN_CMD can use it too.
set -a
# shellcheck disable=SC1090
source "$SHIP_ENV"
set +a

require() {
  local name="$1"
  [[ -n "${!name:-}" ]] || { echo "!! $name is not set in $SHIP_ENV"; exit 1; }
}
require IOS_APP_ID
require IOS_TEAM_ID

# Expand ~ and make a path absolute relative to the project dir.
abs_path() {
  local p="${1/#\~/$HOME}"
  [[ "$p" == /* ]] || p="$PROJECT/$p"
  printf '%s\n' "$p"
}
# First existing file matching a path or glob (empty if none).
first_match() {
  compgen -G "$(abs_path "$1")" | head -1 || true
}

# 0. Decide how the IPA is obtained: custom command, pre-built file, or Flutter defaults.
MODE=""
IPA_PATTERN="${IOS_IPA_PATH:-}"
if [[ -n "${IOS_BUILD_CMD:-}" ]]; then
  MODE=custom
  if [[ -z "$IPA_PATTERN" ]]; then
    if [[ -f "$PROJECT/pubspec.yaml" ]]; then
      IPA_PATTERN="build/ios/ipa/*.ipa"   # Flutter default output
    else
      echo "!! IOS_BUILD_CMD is set but IOS_IPA_PATH is not: set IOS_IPA_PATH in $SHIP_ENV to the IPA your command produces (see docs/build-recipes.md)"
      exit 1
    fi
  fi
elif [[ -n "$IPA_PATTERN" ]]; then
  MODE=prebuilt
  [[ -n "$(first_match "$IPA_PATTERN")" ]] || { echo "!! IOS_IPA_PATH matches no file ($(abs_path "$IPA_PATTERN")) and IOS_BUILD_CMD is not set: build the IPA first, or set IOS_BUILD_CMD (see docs/build-recipes.md)"; exit 1; }
elif [[ -f "$PROJECT/pubspec.yaml" ]]; then
  MODE=flutter                          # Flutter defaults (pubspec.yaml detected)
  IPA_PATTERN="build/ios/ipa/*.ipa"
else
  echo "!! Don't know how to build this project's IPA. Set one of these in $SHIP_ENV:"
  echo "!!   IOS_BUILD_CMD + IOS_IPA_PATH   a command that builds a release IPA, and where it lands"
  echo "!!   IOS_IPA_PATH                   a pre-built IPA to upload as is"
  echo "!!   IOS_CLEAN_CMD                  (optional) a command that clears build caches"
  echo "!! Recipes for common toolchains: docs/build-recipes.md"
  exit 1
fi

REQUIRED=(xcrun asc python3 unzip plutil lipo)
[[ "$MODE" == flutter ]] && REQUIRED+=(flutter)
for cmd in "${REQUIRED[@]}"; do
  command -v "$cmd" >/dev/null || { echo "!! required command not found: $cmd"; exit 1; }
done

KEY_ID="${ASC_KEY_ID:-$(asc auth status --output json 2>/dev/null | python3 -c 'import sys,json;c=json.load(sys.stdin)["credentials"];print((next((x for x in c if x.get("isDefault")),c[0]))["keyId"])' 2>/dev/null || true)}"
ISSUER_ID="${ASC_ISSUER_ID:-$(asc auth issuer-id 2>/dev/null | tr -d '[:space:]' || true)}"
[[ -n "$KEY_ID" ]] || { echo "!! could not resolve the ASC API key ID: run 'asc auth login' (see docs/setup-ios.md) or set ASC_KEY_ID in ship.env"; exit 1; }
[[ -n "$ISSUER_ID" ]] || { echo "!! could not resolve the ASC issuer ID: run 'asc auth login' with --issuer-id or set ASC_ISSUER_ID in ship.env"; exit 1; }

cd "$PROJECT"
echo ">> project: $PROJECT  app: ${IOS_BUNDLE_ID:-$IOS_APP_ID}  team: $IOS_TEAM_ID  key: $KEY_ID  build: $MODE"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [[ "$MODE" == prebuilt ]]; then
  echo ">> using pre-built IPA (no clean, no build)"
else
  # 1. Clean (avoids simulator-arch contamination -> ITMS-90087)
  if [[ $DO_CLEAN -eq 1 ]]; then
    if [[ -n "${IOS_CLEAN_CMD:-}" ]]; then
      echo ">> clean: $IOS_CLEAN_CMD"
      bash -c "$IOS_CLEAN_CMD"
    elif [[ "$MODE" == flutter ]]; then
      echo ">> flutter clean + pub get"
      flutter clean >/dev/null
      flutter pub get >/dev/null
    else
      echo ">> no IOS_CLEAN_CMD set; building without a clean step"
    fi
  fi

  # 2. Disk-export ExportOptions (don't use a destination:upload plist)
  EXPORT_OPTIONS_PLIST="$WORK/ExportOptions_disk.plist"
  export EXPORT_OPTIONS_PLIST
  cat > "$EXPORT_OPTIONS_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>destination</key><string>export</string>
  <key>method</key><string>app-store-connect</string>
  <key>manageAppVersionAndBuildNumber</key><false/>
  <key>signingStyle</key><string>automatic</string>
  <key>teamID</key><string>${IOS_TEAM_ID}</string>
  <key>stripSwiftSymbols</key><true/>
  <key>uploadSymbols</key><true/>
</dict></plist>
EOF

  echo ">> building release IPA (this takes a few minutes)..."
  STAMP="$WORK/build-start"
  touch "$STAMP"
  sleep 1   # mtime checks below have 1-second resolution on some systems
  if [[ "$MODE" == flutter ]]; then
    rm -rf build/ios/ipa && mkdir -p build/ios/ipa
    flutter build ipa --release --export-options-plist="$EXPORT_OPTIONS_PLIST"
  else
    echo ">> build: $IOS_BUILD_CMD"
    bash -c "$IOS_BUILD_CMD"
  fi
fi

IPA=$(first_match "$IPA_PATTERN")
[[ -n "$IPA" && -f "$IPA" ]] || { echo "!! no IPA found at $(abs_path "$IPA_PATTERN") (did the ExportOptions use destination:upload instead of export?)"; exit 1; }
if [[ "$MODE" != prebuilt && ! "$IPA" -nt "$STAMP" ]]; then
  echo "!! $IPA is older than this build: the build did not write a new IPA there. Check IOS_IPA_PATH and the build output."
  exit 1
fi
echo ">> IPA: $IPA"

# 3. Verify version + architectures
mkdir -p "$WORK/ipa"
( cd "$WORK/ipa" && unzip -q "$IPA" ) || { echo "!! could not unzip $IPA; is it a valid IPA?"; exit 1; }
APP_DIR=$(find "$WORK/ipa/Payload" -maxdepth 1 -type d -name '*.app' 2>/dev/null | head -1 || true)
[[ -n "$APP_DIR" && -f "$APP_DIR/Info.plist" ]] || { echo "!! $IPA has no Payload/*.app/Info.plist; is it a valid iOS IPA?"; exit 1; }
VER=$(plutil -extract CFBundleShortVersionString raw "$APP_DIR/Info.plist")
BUILD=$(plutil -extract CFBundleVersion raw "$APP_DIR/Info.plist")
EXE=$(plutil -extract CFBundleExecutable raw "$APP_DIR/Info.plist" 2>/dev/null || basename "$APP_DIR" .app)
echo ">> IPA version: $VER ($BUILD)"
if [[ -n "${VERSION_NAME:-}" && "$VERSION_NAME" != "$VER" ]]; then
  echo "!! IPA version $VER does not match VERSION_NAME=$VERSION_NAME in ship.env"; exit 1
fi
if [[ -n "${BUILD_NUMBER:-}" && "$BUILD_NUMBER" != "$BUILD" ]]; then
  echo "!! IPA build number $BUILD does not match BUILD_NUMBER=$BUILD_NUMBER in ship.env"; exit 1
fi

BAD=0
if lipo -info "$APP_DIR/$EXE" 2>/dev/null | grep -qiE "x86_64|i386|armv7"; then
  echo "!! main executable $EXE has a non-arm64 slice"; BAD=1
fi
for f in "$APP_DIR"/Frameworks/*.framework; do
  [[ -d "$f" ]] || continue
  n=$(basename "$f" .framework); bin="$f/$n"
  if [[ -f "$bin" ]] && lipo -info "$bin" 2>/dev/null | grep -qiE "x86_64|i386|armv7"; then
    echo "!! STRAY ARCH in $n: $(lipo -info "$bin")"; BAD=1
  fi
done
for bin in "$APP_DIR"/Frameworks/*.dylib; do
  [[ -f "$bin" ]] || continue
  if lipo -info "$bin" 2>/dev/null | grep -qiE "x86_64|i386|armv7"; then
    echo "!! STRAY ARCH in $(basename "$bin"): $(lipo -info "$bin")"; BAD=1
  fi
done
if [[ $BAD -ne 0 ]]; then
  echo "!! The IPA contains x86_64 simulator slices (would fail with ITMS-90087)."
  echo "!! Clean the build caches (IOS_CLEAN_CMD, or your toolchain's clean), rebuild WITHOUT --no-clean, and re-run."
  exit 1
fi
echo ">> architecture check: all arm64-only OK"

# 4. Upload via altool (Apple official, reliable)
echo ">> uploading via altool..."
xcrun altool --upload-app --type ios -f "$IPA" --apiKey "$KEY_ID" --apiIssuer "$ISSUER_ID"

echo ">> UPLOAD done. Apple is now processing the build (~5-15 min)."
echo ">> verify with: asc builds list --app $IOS_APP_ID --output table | head -4   (look for build $BUILD, Processing VALID)"
