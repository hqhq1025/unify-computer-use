---
name: libreoffice-java-missing
description: This machine's LibreOffice 7.3 has no Java bridge, so Java-based extensions (e.g. LanguageTool) install but cannot run without sudo.
metadata:
  type: project
---

LibreOffice 7.3.7.2 on this Ubuntu 22.04 box is installed WITHOUT Java support: the packages `libreoffice-java-common`, `ure-java`, `libunoloader-java`, and `liblibreoffice-java` are all absent, so `/usr/lib/libreoffice/program/` has no `javaldx`, no `javavendors.xml`, and an empty `classes/` directory.

Consequence: Java-based .oxt extensions deploy into the user profile and register their menus/toolbars, but their actual components silently do nothing. As of 2026-08-04, LanguageTool 6.3 is installed in this state (extension file kept at `~/Downloads/LanguageTool-stable.oxt`, a user-space Temurin 17 JRE at `~/.local/jvm/jdk-17.0.20+8-jre`).

There is no user-space workaround — the missing files must land in root-owned `/usr/lib/libreoffice/program/`. `user` is in the `sudo` group but sudo requires a password (no NOPASSWD).

**Why:** "Extension installed but does nothing" is invisible from the Extension Manager, which lists LanguageTool as present; without this note the failure is easy to misdiagnose.

**How to apply:** Before installing any Java-based LibreOffice extension here, check for `/usr/lib/libreoffice/program/javaldx`. If missing, ask the user to run `sudo apt-get install -y libreoffice-java-common default-jre-headless` first.
