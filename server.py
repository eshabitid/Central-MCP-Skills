import json
import re
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("central-skills", host="0.0.0.0", port=8000)
SKILLS_DIR = Path("/data/skills").resolve()
DISABLED_FILE = Path("/data/disabled_skills.json")


def get_disabled() -> set[str]:
    if not DISABLED_FILE.exists():
        return set()
    try:
        return set(json.loads(DISABLED_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def parse_frontmatter(content: str) -> dict[str, str]:
    """Ekstrak name dan description dari YAML frontmatter tanpa library tambahan."""
    meta = {"name": "", "description": ""}
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            if key in meta:
                meta[key] = val.strip().strip('"').strip("'")
    return meta


@mcp.tool()
def list_skills() -> list[dict[str, str]]:
    """Menampilkan daftar skill yang AKTIF beserta nama dan deskripsinya."""
    if not SKILLS_DIR.exists():
        return []

    disabled = get_disabled()
    results = []

    for skill_file in sorted(SKILLS_DIR.rglob("SKILL.md")):
        rel_path = skill_file.parent.relative_to(SKILLS_DIR).as_posix()
        if rel_path in disabled:
            continue
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
            meta = parse_frontmatter(text[:2000])
        except OSError:
            meta = {"name": "", "description": ""}

        results.append({
            "path": rel_path,
            "name": meta["name"] or skill_file.parent.name,
            "description": meta["description"] or "Tidak ada deskripsi.",
        })

    return results


@mcp.tool()
def get_skill(skill_path: str) -> str:
    """Mengambil isi SKILL.md dan daftar file pendukung (jika ada) untuk skill tertentu."""
    if skill_path in get_disabled():
        return f"Skill '{skill_path}' sedang dinonaktifkan."

    skill_dir = (SKILLS_DIR / skill_path).resolve()
    target = skill_dir / "SKILL.md"

    if not target.is_relative_to(SKILLS_DIR) or not target.is_file():
        return f"Skill '{skill_path}' tidak ditemukan."

    content = target.read_text(encoding="utf-8", errors="replace")

    # Cek apakah ada file referensi/script pendukung di folder skill
    extra_files = sorted([
        f.relative_to(skill_dir).as_posix()
        for f in skill_dir.rglob("*")
        if f.is_file() and f.name != "SKILL.md" and ".git" not in f.parts
    ])

    if extra_files:
        files_list = "\n".join(f"- {f}" for f in extra_files)
        content += f"\n\n---\n**File pendukung tersedia** (gunakan `get_skill_file` untuk membaca):\n{files_list}"

    return content


@mcp.tool()
def get_skill_file(skill_path: str, file_path: str) -> str:
    """Membaca file pendukung (misal: references/api.md) di dalam folder skill yang aktif."""
    if skill_path in get_disabled():
        return f"Skill '{skill_path}' sedang dinonaktifkan."

    skill_dir = (SKILLS_DIR / skill_path).resolve()
    target = (skill_dir / file_path).resolve()

    if not target.is_relative_to(skill_dir) or not target.is_file():
        return f"File '{file_path}' tidak ditemukan di skill '{skill_path}'."

    return target.read_text(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
