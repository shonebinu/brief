import json
from collections import defaultdict
from pathlib import Path

# {language: {platform: [commands...]}}
commands = defaultdict(lambda: defaultdict(list))

base_path = Path(".")

for dir in base_path.iterdir():
    if dir.is_dir() and dir.name.startswith("pages."):
        lang = dir.name.split(".", 1)[1]

        for platform_dir in dir.iterdir():
            if platform_dir.is_dir():
                platform = platform_dir.name

                for md_file in platform_dir.glob("*.md"):
                    command = md_file.stem
                    commands[lang][platform].append(command)

(base_path / "commands.json").write_text(
    json.dumps(commands, indent=2), encoding="utf-8"
)

print("Saved commands.json")
