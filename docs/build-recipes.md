# Build recipes

The upload scripts don't care how your IPA or AAB is built. They run a build command you give them, pick up the artifact at a path you give them, verify it, and upload it. The Flutter recipe and the native Xcode recipe have been run end to end by the author (a native SwiftUI app uploaded to TestFlight through `IOS_BUILD_CMD`). The Gradle recipe is a standard command that follows the same contract and has not been run. Corrections welcome.

## The contract

Set these in `ship.env` (all optional). Commands run from the project root, with every `ship.env` key in the environment.

| Key | Meaning |
|---|---|
| `IOS_BUILD_CMD` | Builds a release IPA. It can read `$EXPORT_OPTIONS_PLIST`, a disk-export `ExportOptions.plist` the script writes for your `IOS_TEAM_ID` (`destination` = `export`, `method` = `app-store-connect`, automatic signing). |
| `IOS_IPA_PATH` | Where the IPA lands (relative to the project root, or absolute; a glob such as `build/ipa/*.ipa` is fine). Without `IOS_BUILD_CMD`, the script uploads this file as is and skips the build. |
| `IOS_CLEAN_CMD` | Clears build caches before the build. Skipped with `--no-clean`. |
| `ANDROID_BUILD_CMD` | Builds a signed release AAB. |
| `ANDROID_AAB_PATH` | Where the AAB lands. Without `ANDROID_BUILD_CMD`, the script uploads this file as is. |
| `ANDROID_CLEAN_CMD` | Clears build caches before the build. Skipped with `--skip-clean`. |
| `VERSION_NAME`, `BUILD_NUMBER` | The version and build number / versionCode you are shipping. iOS reads both from the IPA and only uses these to double-check it. Android needs `BUILD_NUMBER` for the "already on Play?" check unless the project has a `pubspec.yaml` to read it from. |

Use single quotes around commands in `ship.env`, so that `$EXPORT_OPTIONS_PLIST` is expanded when the build runs rather than when `ship.env` is loaded.

When a build command is set, the script checks that the artifact at `*_PATH` was written by this build, so a stale file is never uploaded. Then the usual gates run: the iOS script refuses an IPA with x86_64 / i386 / armv7 slices, and the Android script refuses an unsigned AAB and a versionCode that is not above the highest one already on the track.

## Recipes

| | Flutter (default when `pubspec.yaml` exists) | Native Xcode / Gradle | Pre-built artifact |
|---|---|---|---|
| `IOS_BUILD_CMD` | `flutter build ipa --release --export-options-plist="$EXPORT_OPTIONS_PLIST"` | see below | unset |
| `IOS_IPA_PATH` | `build/ios/ipa/*.ipa` | `build/ipa/*.ipa` | path to your `.ipa` |
| `IOS_CLEAN_CMD` | `flutter clean && flutter pub get` | see below | unset |
| `ANDROID_BUILD_CMD` | `flutter build appbundle --release` | `./gradlew bundleRelease` | unset |
| `ANDROID_AAB_PATH` | `build/app/outputs/bundle/release/app-release.aab` | `app/build/outputs/bundle/release/app-release.aab` | path to your `.aab` |
| `ANDROID_CLEAN_CMD` | `flutter clean && flutter pub get` | `./gradlew clean` | unset |
| Version | read from `pubspec.yaml` | iOS: from the IPA; Android: set `BUILD_NUMBER` | iOS: from the IPA; Android: set `BUILD_NUMBER` |

### Flutter

Leave every build key unset. The scripts detect `pubspec.yaml` and use the commands in the table above. On Android they also run `flutter pub get` and `flutter analyze` before building, and stop on analyzer errors (not on info-level lints). Set a key only to override one step.

Flutter-specific notes:

- Version: `version: x.y.z+NN` in `pubspec.yaml`. `+NN` becomes both the iOS `CFBundleVersion` and the Android `versionCode`. iOS and Android share it, so it is common to find that a code was already shipped to Play in an earlier session. Check both stores before you bump.
- ITMS-90087 (unsupported architecture): after `flutter run` or `flutter build ios --simulator`, some frameworks (seen with `objective_c.framework`) are cached as x86_64-only simulator slices, and the release archive embeds them. `flutter clean` forces a fresh `pod install` and device build. This is why the default clean step is on.
- `ios/ExportOptions.plist` in many Flutter repos has `destination: upload`, which makes `flutter build ipa` upload by itself and leave no IPA on disk. The scripts pass their own disk-export plist instead.
- Signing: iOS signing is set on the **Runner** target in `ios/Runner.xcworkspace`. Android release signing is usually `android/key.properties`, read by `android/app/build.gradle`. See Flutter's [Build and release an Android app](https://docs.flutter.dev/deployment/android#sign-the-app).
- `Target SDK of artifact is too low`: replace a literal `targetSdkVersion 35` in `android/app/build.gradle` with `flutter.targetSdkVersion` (36 with Flutter 3.38).
- `Android Gradle Plugin version X is lower than Flutter's minimum`: bump AGP, Kotlin, the Gradle wrapper and the JDK together (see android-playstore-submit, "Build failures on stale Android projects"). That bump was verified with Flutter 3.38 and JDK 17.
- Kotlin `Unresolved reference 'PaywallViewFactory'` (or similar) inside a plugin's own sources right after bumping the plugin (e.g. `purchases_ui_flutter`): stale plugin build cache. `rm -rf build/<plugin_name>` and rebuild. A full `flutter clean` also works but is slower.
- Play Billing Library 8.0:
  - RevenueCat: bump `purchases_flutter` and `purchases_ui_flutter` to `^10.11.0`. `Purchases.purchasePackage(package)` is deprecated; use
    ```dart
    final result = await Purchases.purchase(PurchaseParams.package(package));
    final customerInfo = result.customerInfo;
    ```
    Other calls are unchanged (`configure`, `getCustomerInfo`, `getOfferings`, `logIn`, `logOut`, `restorePurchases`, `RevenueCatUI.presentPaywall`). The iOS side then needs a `pod update` on the next iOS build.
  - `in_app_purchase`: upgrade `in_app_purchase_android` to a BillingClient 8 compatible release.
- AdMob with `google_mobile_ads`: the app ID goes into `ios/Runner/Info.plist` and `android/app/src/main/AndroidManifest.xml`; ad unit IDs go into Dart. Use Google's [test ad units](https://developers.google.com/admob/flutter/test-ads) in debug builds (for example, switch on `kReleaseMode`).

### Native Xcode

Archive, then export with the plist the script provides. Replace `MyApp` with your workspace and scheme (use `-project MyApp.xcodeproj` if you have no workspace):

```
IOS_BUILD_CMD='xcodebuild -workspace MyApp.xcworkspace -scheme MyApp -configuration Release -destination "generic/platform=iOS" -archivePath build/MyApp.xcarchive archive && xcodebuild -exportArchive -archivePath build/MyApp.xcarchive -exportOptionsPlist "$EXPORT_OPTIONS_PLIST" -exportPath build/ipa'
IOS_IPA_PATH=build/ipa/*.ipa
IOS_CLEAN_CMD='rm -rf build/MyApp.xcarchive build/ipa && xcodebuild -workspace MyApp.xcworkspace -scheme MyApp clean'
```

If the export fails because a provisioning profile is missing, add `-allowProvisioningUpdates` to the `-exportArchive` call (Xcode must be signed in to your team).

If your project signs manually (a fixed provisioning profile and certificate in the Release configuration), `$EXPORT_OPTIONS_PLIST` is not enough because it uses automatic signing. Write your own export options plist with `signingStyle` = `manual`, your `signingCertificate`, and a `provisioningProfiles` map of bundle ID to profile name, keep `method` = `app-store-connect` and `destination` = `export`, and pass that file to `-exportArchive` instead. Verified this way, the build, the arm64 check and the altool upload all pass.

### Native Gradle

```
ANDROID_BUILD_CMD='./gradlew bundleRelease'
ANDROID_AAB_PATH=app/build/outputs/bundle/release/app-release.aab
ANDROID_CLEAN_CMD='./gradlew clean'
BUILD_NUMBER=12        # = versionCode in app/build.gradle(.kts); update it each release
```

Use the module name in place of `app` if your application module is named differently.

### Pre-built artifact

Built somewhere else (CI, another machine, a different tool)? Set only the path, and the script skips the clean and build steps:

```
IOS_IPA_PATH=/path/to/MyApp.ipa
ANDROID_AAB_PATH=/path/to/app-release.aab
BUILD_NUMBER=12        # Android: the AAB's versionCode
```

The IPA architecture check, the AAB signature check and the versionCode check still run.
