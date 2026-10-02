# Vendored Inno Setup translations

Inno Setup ships the official translations under `compiler:Languages\` (English,
Brazilian Portuguese, Spanish and Russian come from there). Simplified Chinese is not
one of them, so `voicemate-companion.iss` takes it from this folder instead.

## ChineseSimplified.isl

- Translation: "Inno Setup version 6.5.0+ Chinese Simplified messages", maintained by
  Zhenghan Yang (https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation).
- Listed as a user-contributed translation on the official translations page:
  https://jrsoftware.org/files/istrans/
- This copy is the one Inno Setup's own repository carries for the release the
  installer is built with (6.7.3), unchanged:
  https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl
  (SHA-256 `7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278`).
- License: Inno Setup translations are distributed under the Inno Setup license
  (https://jrsoftware.org/files/is/license.txt), copyright Jordan Russell and Martijn
  Laan; the translation is the work of its maintainer named above. Keep the file's
  header comments when updating it.

To update it after an Inno Setup upgrade, take the file from the matching `is-<version>`
tag of https://github.com/jrsoftware/issrc (or from the translations page), replace it
here, update the version and checksum above, and run `make companion-installer`: ISCC
warns about messages the translation lacks (those show in English).
