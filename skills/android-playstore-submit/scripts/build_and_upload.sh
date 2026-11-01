#!/usr/bin/env bash
# Build an Android release AAB (or take a pre-built one) and upload it to Google Play via
# fastlane supply (Play Developer API), reliably.
#
# Usage:
#   bash build_and_upload.sh <project-dir> [--track production|internal|alpha|beta]
#        [--status completed|draft|inProgress|halted] [--notes /path/notes.txt]
#        [--notes-locale en-US] [--skip-clean] [--confirm-production]
#
# Inputs come from <project-dir>/ship.env (override the path with SHIP_ENV=/path/to/ship.env):
#   ANDROID_PACKAGE            (required) applicationId, e.g. com.example.myapp
#   PLAY_SERVICE_ACCOUNT_JSON  (required) path to the service-account JSON key
#   ANDROID_BUILD_CMD          (optional) command that produces a signed release AAB, run from <project-dir>
#   ANDROID_AAB_PATH           (optional) path (or glob) of the AAB, relative to <project-dir> or absolute.
#                              Without ANDROID_BUILD_CMD it names a pre-built AAB: the build is skipped.
#   ANDROID_CLEAN_CMD          (optional) command run before building to clear build caches
#   BUILD_NUMBER               (optional) the AAB's versionCode, used for the "already on Play?" check
#   VERSION_NAME               (optional) the versionName, used in log messages
# With none of the ANDROID_*_CMD / ANDROID_AAB_PATH keys set, a project with pubspec.yaml uses
# the Flutter defaults; otherwise the script stops and asks for them (see docs/build-recipes.md).
# The versionCode comes from BUILD_NUMBER, else from pubspec.yaml (x.y.z+NN), else the script stops.
#
# Flags:
#   --track               default: production
#   --status              default: completed (= send for review, full rollout on approval)
#   --notes               plain-text release notes (<=500 chars); if omitted, changelogs are skipped
#   --notes-locale        Play locale for the notes; default: en-US
#   --skip-clean          skip the clean step
#   --confirm-production  REQUIRED when --track production with --status completed/inProgress.
#                         Pass it only after the user explicitly approved a production release.
#
# Exits non-zero on any failure (missing config, analyze errors, build errors,
# unsigned AAB, or a Google Api Error from fastlane).
set -euo pipefail

# print the header comment block above as help
usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; }

PROJECT=""
TRACK="production"
STATUS="completed"
NOTES_FILE=""
NOTES_LOCALE="en-US"
SKIP_CLEAN=0
CONFIRM_PRODUCTION=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="${2:?--track needs a value}"; shift 2 ;;
    --status) STATUS="${2:?--status needs a value}"; shift 2 ;;
    --notes) NOTES_FILE="${2:?--notes needs a file path}"; shift 2 ;;
    --notes-locale) NOTES_LOCALE="${2:?--notes-locale needs a value}"; shift 2 ;;
    --skip-clean) SKIP_CLEAN=1; shift ;;
    --confirm-production) CONFIRM_PRODUCTION=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "!! unknown flag: $1"; usage; exit 1 ;;
    *) [[ -z "$PROJECT" ]] || { echo "!! unexpected argument: $1"; exit 1; }; PROJECT="$1"; shift ;;
  esac
done
[[ -n "$PROJECT" ]] || { usage; exit 1; }
PROJECT="$(cd "$PROJECT" && pwd)"

case "$TRACK" in production|internal|alpha|beta) ;; *) echo "!! invalid --track: $TRACK"; exit 1 ;; esac
case "$STATUS" in completed|draft|inProgress|halted) ;; *) echo "!! invalid --status: $STATUS"; exit 1 ;; esac

if [[ "$TRACK" == "production" && ( "$STATUS" == "completed" || "$STATUS" == "inProgress" ) && $CONFIRM_PRODUCTION -ne 1 ]]; then
  echo "!! Refusing to release to production without --confirm-production."
  echo "!! Ask the user for explicit approval first, or use --track internal / --status draft."
  exit 1
fi

SHIP_ENV="${SHIP_ENV:-$PROJECT/ship.env}"
[[ -f "$SHIP_ENV" ]] || { echo "!! ship.env not found at $SHIP_ENV (copy ship.env.example to your app root and fill it in)"; exit 1; }
# Export everything ship.env defines so ANDROID_BUILD_CMD / ANDROID_CLEAN_CMD can use it too.
set -a
# shellcheck disable=SC1090
source "$SHIP_ENV"
set +a

require() {
  local name="$1"
  [[ -n "${!name:-}" ]] || { echo "!! $name is not set in $SHIP_ENV"; exit 1; }
}
require ANDROID_PACKAGE
require PLAY_SERVICE_ACCOUNT_JSON

JSON_KEY="${PLAY_SERVICE_ACCOUNT_JSON/#\~/$HOME}"
# Only check that the key exists. Never print or parse its contents (it holds a private key).
[[ -f "$JSON_KEY" ]] || { echo "!! PLAY_SERVICE_ACCOUNT_JSON points to a missing file: $JSON_KEY (see docs/setup-android.md)"; exit 1; }

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

# Decide how the AAB is obtained: custom command, pre-built file, or Flutter defaults.
MODE=""
AAB_PATTERN="${ANDROID_AAB_PATH:-}"
FLUTTER_AAB="build/app/outputs/bundle/release/app-release.aab"
if [[ -n "${ANDROID_BUILD_CMD:-}" ]]; then
  MODE=custom
  if [[ -z "$AAB_PATTERN" ]]; then
    if [[ -f "$PROJECT/pubspec.yaml" ]]; then
      AAB_PATTERN="$FLUTTER_AAB"          # Flutter default output
    else
      echo "!! ANDROID_BUILD_CMD is set but ANDROID_AAB_PATH is not: set ANDROID_AAB_PATH in $SHIP_ENV to the AAB your command produces (see docs/build-recipes.md)"
      exit 1
    fi
  fi
elif [[ -n "$AAB_PATTERN" ]]; then
  MODE=prebuilt
  [[ -n "$(first_match "$AAB_PATTERN")" ]] || { echo "!! ANDROID_AAB_PATH matches no file ($(abs_path "$AAB_PATTERN")) and ANDROID_BUILD_CMD is not set: build the AAB first, or set ANDROID_BUILD_CMD (see docs/build-recipes.md)"; exit 1; }
elif [[ -f "$PROJECT/pubspec.yaml" ]]; then
  MODE=flutter                            # Flutter defaults (pubspec.yaml detected)
  AAB_PATTERN="$FLUTTER_AAB"
else
  echo "!! Don't know how to build this project's AAB. Set one of these in $SHIP_ENV:"
  echo "!!   ANDROID_BUILD_CMD + ANDROID_AAB_PATH   a command that builds a signed release AAB, and where it lands"
  echo "!!   ANDROID_AAB_PATH                       a pre-built AAB to upload as is"
  echo "!!   ANDROID_CLEAN_CMD                      (optional) a command that clears build caches"
  echo "!! Recipes for common toolchains: docs/build-recipes.md"
  exit 1
fi

REQUIRED=(fastlane jarsigner)
[[ "$MODE" == flutter ]] && REQUIRED+=(flutter)
for cmd in "${REQUIRED[@]}"; do
  command -v "$cmd" >/dev/null || { echo "!! required command not found: $cmd"; exit 1; }
done
export FASTLANE_SKIP_UPDATE_CHECK=1

# fastlane is run from a neutral directory so a stray Fastfile in the app repo is not picked up.
NEUTRAL=$(mktemp -d)
trap 'rm -rf "$NEUTRAL"' EXIT

cd "$PROJECT"

# 1. Version: BUILD_NUMBER / VERSION_NAME from ship.env, else pubspec.yaml (Flutter maps +NN to versionCode)
BUMP_HINT="bump the versionCode in your build config (and BUILD_NUMBER in ship.env)"
VERSION_CODE="${BUILD_NUMBER:-}"
VERSION_NAME="${VERSION_NAME:-}"
if [[ -f pubspec.yaml && ( -z "$VERSION_CODE" || -z "$VERSION_NAME" ) ]]; then
  VERSION_LINE=$(grep -E '^version:' pubspec.yaml || true)
  [[ -n "$VERSION_LINE" ]] || { echo "!! no 'version:' line in pubspec.yaml (or set BUILD_NUMBER / VERSION_NAME in ship.env)"; exit 1; }
  [[ -n "$VERSION_NAME" ]] || VERSION_NAME=$(echo "$VERSION_LINE" | sed -E 's/^version:[[:space:]]*([^+]+)\+.*/\1/' | tr -d '[:space:]')
  if [[ -z "$VERSION_CODE" ]]; then
    VERSION_CODE=$(echo "$VERSION_LINE" | sed -E 's/^version:.*\+([0-9]+).*/\1/' | tr -d '[:space:]')
    [[ "$VERSION_CODE" =~ ^[0-9]+$ ]] || { echo "!! pubspec.yaml version must look like x.y.z+NN (got: $VERSION_LINE)"; exit 1; }
    BUMP_HINT="bump +NN in pubspec.yaml"
  fi
fi
[[ -n "$VERSION_CODE" ]] || { echo "!! unknown versionCode: set BUILD_NUMBER (and VERSION_NAME) in $SHIP_ENV to the versionCode / versionName of the AAB you are uploading"; exit 1; }
[[ "$VERSION_CODE" =~ ^[0-9]+$ ]] || { echo "!! BUILD_NUMBER must be an integer versionCode (got: $VERSION_CODE)"; exit 1; }
echo ">> package: $ANDROID_PACKAGE  version: ${VERSION_NAME:-?} ($VERSION_CODE)  track: $TRACK  status: $STATUS  build: $MODE"

# 2. Pre-check: versionCode must exceed everything already on the track
CODES=$(cd "$NEUTRAL" && fastlane run google_play_track_version_codes \
  package_name:"$ANDROID_PACKAGE" track:"$TRACK" json_key:"$JSON_KEY" 2>&1 \
  | grep -oE 'Result: \[[0-9, ]*\]' | grep -oE '[0-9]+' || true)
MAX_CODE=$(echo "$CODES" | sort -n | tail -1)
if [[ -n "${MAX_CODE:-}" ]]; then
  echo ">> highest versionCode on $TRACK: $MAX_CODE"
  if [[ "$VERSION_CODE" -le "$MAX_CODE" ]]; then
    echo "!! versionCode $VERSION_CODE is not greater than $MAX_CODE already on Play; $BUMP_HINT"
    exit 1
  fi
fi

if [[ "$MODE" == prebuilt ]]; then
  echo ">> using pre-built AAB (no clean, no build)"
else
  # 3. Flutter defaults only: pub get + analyze (fail on errors only)
  if [[ "$MODE" == flutter ]]; then
    echo ">> flutter pub get"
    flutter pub get >/dev/null

    echo ">> flutter analyze"
    # flutter analyze exits non-zero even for info-level lints, so judge by content:
    # only "error •" lines abort the build.
    ANALYZE_OUT=$(flutter analyze 2>&1 || true)
    echo "$ANALYZE_OUT" | tail -20
    if echo "$ANALYZE_OUT" | grep -qE '^\s*error •'; then
      echo "!! flutter analyze reported errors, aborting"
      exit 1
    fi
  fi

  if [[ $SKIP_CLEAN -ne 1 ]]; then
    if [[ -n "${ANDROID_CLEAN_CMD:-}" ]]; then
      echo ">> clean: $ANDROID_CLEAN_CMD"
      bash -c "$ANDROID_CLEAN_CMD"
    elif [[ "$MODE" == flutter ]]; then
      echo ">> flutter clean"
      flutter clean >/dev/null
      flutter pub get >/dev/null
    else
      echo ">> no ANDROID_CLEAN_CMD set; building without a clean step"
    fi
  fi

  # 4. Build release AAB
  echo ">> building release AAB (this takes a few minutes)..."
  STAMP="$NEUTRAL/build-start"
  touch "$STAMP"
  sleep 1   # mtime checks below have 1-second resolution on some systems
  if [[ "$MODE" == flutter ]]; then
    flutter build appbundle --release
  else
    echo ">> build: $ANDROID_BUILD_CMD"
    bash -c "$ANDROID_BUILD_CMD"
  fi
fi

AAB=$(first_match "$AAB_PATTERN")
[[ -n "$AAB" && -f "$AAB" ]] || { echo "!! no AAB found at $(abs_path "$AAB_PATTERN")"; exit 1; }
if [[ "$MODE" != prebuilt && ! "$AAB" -nt "$STAMP" ]]; then
  echo "!! $AAB is older than this build: the build did not write a new AAB there. Check ANDROID_AAB_PATH and the build output."
  exit 1
fi
echo ">> AAB: $AAB"

# 5. Verify signing
echo ">> verifying signature"
# capture first: `jarsigner | grep -q` under pipefail can misreport via SIGPIPE
VERIFY_OUT=$(jarsigner -J-Duser.language=en -verify "$AAB" 2>&1 || true)
# the English text is forced above; the Japanese pattern is a fallback for localized JDKs
if ! echo "$VERIFY_OUT" | grep -qiE "jar verified|jarが検証されました"; then
  echo "$VERIFY_OUT" | tail -5
  echo "!! AAB is not signed correctly; check the release signingConfig in your app's Gradle build (and the keystore / key.properties it reads)"
  exit 1
fi
echo ">> signature OK"

# 6. Release notes (optional)
UPLOAD_ARGS=(skip_upload_metadata:true skip_upload_images:true skip_upload_screenshots:true skip_upload_apk:true)
if [[ -n "$NOTES_FILE" ]]; then
  [[ -f "$NOTES_FILE" ]] || { echo "!! notes file not found: $NOTES_FILE"; exit 1; }
  META_DIR="$NEUTRAL/metadata"
  mkdir -p "$META_DIR/android/$NOTES_LOCALE/changelogs"
  cp "$NOTES_FILE" "$META_DIR/android/$NOTES_LOCALE/changelogs/${VERSION_CODE}.txt"
  UPLOAD_ARGS+=(skip_upload_changelogs:false metadata_path:"$META_DIR/android")
  echo ">> release notes staged for $NOTES_LOCALE (versionCode $VERSION_CODE)"
else
  UPLOAD_ARGS+=(skip_upload_changelogs:true)
  echo ">> no --notes given, skipping changelogs"
fi

# 7. Upload via fastlane supply
echo ">> uploading via fastlane run upload_to_play_store..."
set +e
OUT=$(cd "$NEUTRAL" && fastlane run upload_to_play_store \
  package_name:"$ANDROID_PACKAGE" \
  aab:"$AAB" \
  track:"$TRACK" release_status:"$STATUS" \
  json_key:"$JSON_KEY" \
  "${UPLOAD_ARGS[@]}" 2>&1)
RC=$?
set -e
# drop the giant fastlane options table from the log
echo "$OUT" | grep -vE "^\s*$|^\|" || true

if [[ $RC -ne 0 ]]; then
  if echo "$OUT" | grep -q "does not have permission"; then
    echo "!! HINT: invite the service account to THIS app in Play Console -> Users and permissions (see SKILL.md 'Prerequisites')"
  elif echo "$OUT" | grep -qi "Billing Library"; then
    echo "!! HINT: upgrade the in-app purchase SDK to a release that bundles Play Billing Library 8+ — see SKILL.md 'Play-side rejections' #1"
  elif echo "$OUT" | grep -qi "child safety"; then
    echo "!! HINT: change the Play Store category away from Social/Dating, or complete the Child safety standards declaration — see SKILL.md 'Play-side rejections' #2"
  elif echo "$OUT" | grep -qi "Target SDK"; then
    echo "!! HINT: raise targetSdk in the app's Gradle config to the level Play currently requires — see SKILL.md 'Play-side rejections' #3"
  elif echo "$OUT" | grep -qi "already been used"; then
    echo "!! HINT: versionCode already used on another track; $BUMP_HINT"
  fi
  exit 1
fi

echo ">> UPLOAD done. track=$TRACK status=$STATUS version=${VERSION_NAME:-?} ($VERSION_CODE)"
