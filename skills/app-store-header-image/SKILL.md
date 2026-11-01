---
name: app-store-header-image
description: >-
  Create the App Store product page header image (the wide banner at the top of the
  product page) and the search-results creative asset, upload them to the Asset Library
  with the asc CLI, send them to review, and assign them to the product page. A new
  feature since 2026-10-05 with little documentation and several traps: a live version
  only accepts APPROVED assets, any transparency is rejected, and older asc builds do not
  support it. Use for "ヘッダ画像を上げて", "App Store のヘッダー", "製品ページのヘッダ",
  "検索結果の画像", "Asset Library", "クリエイティブアセット", "product page header",
  "header image for the App Store", "search results asset". Works for any app. macOS
  recommended (asc).
---

# App Store header image (Asset Library)

How to handle, with the asc CLI, the wide image/video shown at the very top of the
product page and the image shown in search results. This is an optional slot separate
from screenshots; it does not replace them.

The authoritative, up-to-date reference is the guide shipped with asc: `asc docs` and
https://github.com/rorkai/App-Store-Connect-CLI/blob/main/guides/asset-library.mdx

`APP_ID` below is `IOS_APP_ID` from `ship.env` (load with `set -a; source ship.env; set +a`).

## Know this first

- **Only shown on iOS 27 / iPadOS 27 and later.** Users on older OS versions don't see it.
- **Assigned per localization.** If the title lettering is in one language, assign it
  only to that locale. To use the same art for every locale, make a version without text.
- **A live (already approved) version only accepts assets in the APPROVED state.**
  Assigning right after upload fails with the following error. This is by design, not a
  mistake in your steps:
  `The asset must be in the APPROVED state because the parent is already approved.`
- **An image can be sent to review on its own.** No new app version is needed (only for
  apps with at least one approved version; for an app's first version, include the asset
  in that version's submission).
- **Final publishing happens on the web.** After approval, to reflect it on the live page,
  someone has to click "Next → Publish" in App Store Connect. There is no CLI publish
  command.

## 0. Check the asc version

Asset Library needs **asc 5.12.0 or later** (video and list filtering need 5.13.0+).

```bash
asc version
asc asset-library --help          # if this doesn't exist, asc is too old
brew update && brew upgrade asc   # run `brew update` first, or brew may stop at a stale version
```

asc ships very fast (a new major roughly every month); leave it for a few months and you
are several majors behind. After upgrading, watch for changed flags in asc commands that
other skills use.

## 1. Identify the app and its library

```bash
asc apps list --output table                       # APP_ID
asc asset-library view --app "$APP_ID"             # data.id is LIBRARY_ID (so far it has equaled APP_ID)
asc asset-library specs --output table             # current sizes; trust specs over the table below
asc asset-library images list --library-id LIBRARY_ID --category CREATIVE_ASSETS --output table
```

Existing screenshots are imported into the library automatically, so the full list is
long. Filter with `--category CREATIVE_ASSETS`.

## 2. Make the image

| Use | Size | Ratio | placement type |
|---|---|---|---|
| Header only | exactly 3840×1646 | 21:9 | `PRODUCT_PAGE_HEADER_ASSET` |
| Header + search results | exactly 5244×2950 | 16:9 | both |
| Search results only | 1920×1280 to 3840×2560 | 3:2 | `APP_STORE_SEARCH_RESULTS_ASSET` |

Video: header 21:9, search 3:2, 5–30 seconds, 30/60 fps. Requires `ffprobe`.

**No transparency.** The mere presence of an alpha channel gets the asset rejected, even
if it looks fully opaque. Always export RGB.

Design guidelines (what worked in practice):
- **Build it from the app's own assets where possible.** Using the in-app background,
  hero object, and UI ornaments keeps it faithful to the real app. Fully AI-generated art
  is flashier but tends to look unlike the actual product.
- **Use the logo the app actually uses** for the title (if there is an SVG, render it
  directly with `rsvg-convert -w 2000` so it stays sharp). Don't replace it with
  generated lettering.
- **Don't add extra text.** Title plus hero object is enough; taglines and character
  names were judged unnecessary.
- When upscaling small marks (brush strokes, ink splashes), re-level the alpha after
  scaling to tighten the edges again.
- Keep important elements away from the edges. How much gets cropped at each edge, and
  where icons/buttons overlap, has **not yet been verified on a device**.
- If you use an image-generation model: most accept any width/height that are multiples
  of 16. Generate 3840×1648 and trim 1 px top and bottom (`check_header.py --fix` does it).

`scripts/compose_example.py` is a real compositing script turned into a template: tile
an in-app texture and darken it, add a warm spotlight and vignette, drop a hero object
with a shadow, overlay the SVG logo. Run it against a directory of your own assets
(`background.*`, optional `hero.*`, `title.svg`/`title.png`, `brush.*`) and adjust the
positions in `build()` for your app:

```bash
python3 <skill-dir>/scripts/compose_example.py path/to/assets --out header.png --preview preview.jpg
```

Check every export:

```bash
python3 <skill-dir>/scripts/check_header.py header.png
python3 <skill-dir>/scripts/check_header.py raw.png --fix header.png   # flatten alpha, trim stray pixels
```

The check uses only the Python standard library; `--fix` and `compose_example.py` need
Pillow (`pip install pillow`).

**Always show the image to the user before uploading.** If the session can't send files
to the chat, open it in a local viewer (`open header.png` on macOS) and tell the user you
couldn't attach it. Keep working files in an untracked place in the repo (e.g.
`.asc/header/`). When overwriting copies, use `/bin/cp -f` (`cp` is often aliased to
`cp -i`, which waits for a confirmation and hangs an agent).

## 3. Upload

```bash
asc asset-library images upload --library-id LIBRARY_ID --file header.png --pretty
```

Note the returned `imageId`. Success means `specId` matches the intended spec (the 21:9
one for header-only) and `state` is `PREPARE_FOR_SUBMISSION`. Nobody can see it yet.
The library name is the file name; rename if unclear:
`asc asset-library images rename --id IMAGE_ID --name "Header 2026-10"`.

A failed upload's reserved ID is not cleaned up automatically. Inspect it with
`asc asset-library images view --id IMAGE_ID`, then retry or remove it with
`asc asset-library images delete --id IMAGE_ID --confirm`.

## 4. Send to review

Submitting to review is an outward-facing action — **get the user's explicit approval
first.** An in-progress submission can block a standalone submission, so check first:

```bash
asc review submissions-list --app "$APP_ID" --output table   # all COMPLETE means you're clear
asc review submissions-create --app "$APP_ID" --platform IOS            # data.id is SUBMISSION_ID
asc review items add --submission SUBMISSION_ID \
  --item-type appAssetLibraryImages --item-id IMAGE_ID                  # videos: appAssetLibraryVideos
asc review submissions-submit --id SUBMISSION_ID --confirm
```

An agent harness's autonomous-mode safety check may block this submission. If it does,
don't try to work around it: put the real IDs into the three commands above and hand
them to the user to run.

To withdraw: `asc review submissions-update --id SUBMISSION_ID --canceled=true --confirm`.

## 5. Assign after approval

```bash
asc asset-library images view --id IMAGE_ID                 # state must be APPROVED
asc versions list --app "$APP_ID" --output table            # VERSION_ID of the live version
asc localizations list --version VERSION_ID --output table  # LOCALIZATION_ID per locale
asc localizations placements create --localization-id LOCALIZATION_ID \
  --image-id IMAGE_ID --placement-type PRODUCT_PAGE_HEADER_ASSET --pretty
asc localizations placements list --localization-id LOCALIZATION_ID --include image,video
```

`--localization-id` is the **per-locale ID**, not the version. Don't pass the parent's
(version / custom page) ID. To replace an existing header, use
`placements swap --placement-id OLD_ID ... --confirm`. To just remove it,
`placements delete --id PLACEMENT_ID --confirm` (the library image stays).

Custom product pages: `asc product-pages custom-pages localizations placements`.
Product page optimization (A/B tests): `asc product-pages experiments treatments localizations placements`.
In-app events: `asc app-events localizations placements` (`EVENT_CARD_ASSET` / `EVENT_DETAILS_PAGE_ASSET`).

Then have the user click "Next → Publish" in App Store Connect on the web.
A placement showing `ACTIVE` is not proof that it is published.

### Shipping it with the next version instead

If there's no rush, assign it to the next version's localization (one still being edited,
not yet submitted). It is then reviewed together with that version, and the standalone
review (step 4) is not needed.

## Not yet verified

As of 2026-10-08 the following had not been exercised end to end. Update this section
when they are.

- How many days a standalone image takes to be approved, and what kind of art gets rejected
- The full path from assignment after approval → Publish on the web → visible on the store
- On-device appearance (edge cropping, where the icon/buttons overlap, iPad vs iPhone)
- The complete flow for a video header
