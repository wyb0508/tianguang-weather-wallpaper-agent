"""Build the portable macOS generator with its five skills and offline models."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
NAME = "天光天气壁纸 Agent"
TOP = ("README.md", "AGENTS.md", ".gitignore", "requirements.txt", "agent.py", "delivery.py",
       "skill_pipeline.py", "生成天气壁纸.command", "engine", "models", "scripts", "skills", "templates", "tests")


def package():
    with TemporaryDirectory(dir=ROOT) as directory:
        temp = Path(directory)/"agent.zip"
        with ZipFile(temp, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for top in TOP:
                path = ROOT/top
                for item in sorted(path.rglob("*")) if path.is_dir() else [path]:
                    relative = item.relative_to(ROOT)
                    if (not item.is_file() or any(part in ("__pycache__", ".venv", ".DS_Store") for part in relative.parts)
                            or relative.parts[:4] in (("skills","wallpaper-qa","examples","clean"), ("skills","wallpaper-qa","examples","faults"))):
                        continue
                    archive.write(item, str(Path(NAME)/relative))
        with ZipFile(temp) as archive:
            if archive.testzip() is not None:
                raise ValueError("归档校验失败")
        # Keep the former archive until the replacement is complete and verified.
        out = ROOT/(NAME+".zip")
        os.chmod(temp, 0o644)
        os.replace(temp, out)
        return out


if __name__=="__main__":
    print(package())
