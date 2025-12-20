import json
import shutil
import threading
from collections import defaultdict
from pathlib import Path
import gi
import langcodes

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Soup


class PageManager:
    TLDR_PAGES_ZIP_URL = (
        "https://github.com/tldr-pages/tldr/archive/refs/heads/main.zip"
    )

    def __init__(self):
        # /app is read-only at runtime.
        # ${FLATPAK_DEST} in flatpak manifest resolves to /app
        self.system_data_dir = Path("/app/share/tldr/")
        self.cache_dir = Path(GLib.get_user_cache_dir()) / "brief"
        self.local_data_dir = self.cache_dir / "tldr"
        self.zip_path = self.cache_dir / "tldr.zip"

        self.settings = Gio.Settings.new("io.github.shonebinu.Brief")
        self.session = Soup.Session.new()

    def _get_data_dir(self):
        return (
            self.local_data_dir
            if self.local_data_dir.exists()
            else self.system_data_dir
        )

    def get_commands_map(self):
        return json.loads(
            (self._get_data_dir() / "commands.json").read_text(encoding="utf-8")
        )

    def get_available_languages(self):
        languages = [
            (langcodes.get(lang).autonym().title(), lang)
            for lang in self.get_commands_map()
        ]

        return sorted(languages, key=lambda x: x[0])

    def get_available_platforms(self):
        pretty_names = {
            "osx": "macOS",
            "sunos": "SunOS",
            "cisco-ios": "Cisco iOS",
            "dos": "DOS",
            "freebsd": "FreeBSD",
            "netbsd": "NetBSD",
            "openbsd": "OpenBSD",
            "android": "Android",
            "windows": "Windows",
            "linux": "Linux",
            "common": "Common",
        }

        platforms = [
            (pretty_names.get(plat, plat.replace("-", " ").title()), plat)
            for plat in self.get_commands_map().get("en", {})
        ]

        return sorted(platforms, key=lambda x: x[0])

    def get_all_commands(self):
        commands = defaultdict(lambda: defaultdict(list))
        enabled_langs = self.settings.get_strv("languages")
        enabled_plats = self.settings.get_strv("platforms")

        all_commands = self.get_commands_map()

        for lang in enabled_langs:
            for plat in enabled_plats:
                if plat in all_commands.get(lang, {}):
                    commands[lang][plat] = all_commands[lang][plat]

        return dict(commands)

    def get_page(self, lang_code, platform, command):
        filepath = (
            self._get_data_dir() / f"pages.{lang_code}" / platform / f"{command}.md"
        )

        if filepath.exists():
            return filepath.read_text(encoding="utf-8")

        return f"Command '{command}' not found in path '{filepath}'."

    def update_cache(self, progress_cb, finished_cb):
        self.progress_cb = progress_cb
        self.finished_cb = finished_cb
        self.downloaded = 0

        self.msg = Soup.Message.new("GET", self.TLDR_PAGES_ZIP_URL)

        self.msg.connect("got-body-data", self._on_got_body_data)

        self.session.send_async(
            self.msg,
            GLib.PRIORITY_DEFAULT,
            None,
            self._on_response_finished,
            None,
        )

    def _on_got_body_data(self, msg, chunk):
        self.downloaded += chunk
        total = msg.get_response_headers().get_content_length()

        fraction = 0 if not total else self.downloaded / total

        GLib.idle_add(
            self.progress_cb,
            fraction,
            f"Downloading... {(fraction * 100):.0f}% ({(self.downloaded / 1024 / 1024):.2f} MB)",
        )

    def _on_response_finished(self, session, result, data):
        try:
            stream = session.send_finish(result)

            file = Gio.File.new_for_path(str(self.zip_path))
            output = file.replace(
                None,
                False,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                None,
            )

            output.splice_async(
                stream,
                Gio.OutputStreamSpliceFlags.CLOSE_SOURCE
                | Gio.OutputStreamSpliceFlags.CLOSE_TARGET,
                GLib.PRIORITY_DEFAULT,
                None,
                self._on_splice_finished,
                None,
            )

        except GLib.Error as e:
            GLib.idle_add(self.finished_cb, False, e.message)

    def _on_splice_finished(self, output, result, data):
        try:
            output.splice_finish(result)
            GLib.idle_add(self.progress_cb, 1.0, "Extracting...")

            threading.Thread(target=self._extract_in_thread, daemon=True).start()

        except GLib.Error as e:
            GLib.idle_add(self.finished_cb, False, e.message)

    def _extract_in_thread(self):
        try:
            self.process_zip()
            GLib.idle_add(self.finished_cb, True, "Cache updated successfully")
        except Exception as e:
            GLib.idle_add(self.finished_cb, False, str(e))

    def process_zip(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        extract_temp = Path(self.cache_dir) / "temp_extract"
        shutil.rmtree(extract_temp, ignore_errors=True)

        shutil.unpack_archive(self.zip_path, extract_temp)

        content_root = next(extract_temp.iterdir())
        content_root = Path(content_root)

        pages_en, pages = content_root / "pages.en", content_root / "pages"
        if pages_en.exists():
            pages_en.unlink()
        if pages.exists():
            pages.rename(pages_en)

        commands = defaultdict(lambda: defaultdict(list))

        for entry in content_root.iterdir():
            if entry.is_dir() and entry.name.startswith("pages."):
                lang = entry.name.split(".", 1)[1]

                for platform_dir in entry.iterdir():
                    if platform_dir.is_dir():
                        platform = platform_dir.name

                        for md_file in platform_dir.glob("*.md"):
                            command = md_file.stem
                            commands[lang][platform].append(command)
            else:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()

        (content_root / "commands.json").write_text(
            json.dumps(commands, indent=2), encoding="utf-8"
        )

        shutil.rmtree(self.local_data_dir, ignore_errors=True)
        shutil.move(str(content_root), self.local_data_dir)

        shutil.rmtree(extract_temp, ignore_errors=True)
        Path(self.zip_path).unlink()
