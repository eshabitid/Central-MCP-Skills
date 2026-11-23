import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus
from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI()

DATA_DIR = Path("/data")
SKILLS_DIR = DATA_DIR / "skills"
REPOS_FILE = DATA_DIR / "repos.json"
DISABLED_FILE = DATA_DIR / "disabled_skills.json"
ALWAYS_ON_FILE = DATA_DIR / "always_on.json"
PROFILES_FILE = DATA_DIR / "profiles.json"
ACTIVE_PROFILE_FILE = DATA_DIR / "active_profile.txt"

SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "21600"))  # Default 6 jam (0 = nonaktif)
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PROFILES = {
    "Frappe": [],
    "PHP (Laravel/CI)": [],
    "WordPress": [],
    "Docker": [],
}

# Lock untuk mencegah race condition antara UI dan background auto-sync
data_lock = threading.Lock()
git_lock = threading.Lock()

GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data):
    """Atomic write agar file JSON tidak korup saat dibaca bersamaan oleh MCP server."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_all_skills() -> list[str]:
    return sorted([
        f.parent.relative_to(SKILLS_DIR).as_posix()
        for f in SKILLS_DIR.rglob("SKILL.md")
    ])


def repo_slug(url: str) -> str:
    """Gunakan format owner__repo dan sanitasi karakter agar aman untuk nama folder."""
    clean = url.rstrip("/").removesuffix(".git")
    parts = [p for p in clean.split("/") if p]
    raw = f"{parts[-2]}__{parts[-1]}" if len(parts) >= 2 else (parts[-1] if parts else "repo")
    return re.sub(r"[^a-zA-Z0-9._-]", "_", raw).strip(".") or "repo"


def cleanup_orphaned_skills():
    """Hapus jejak skill yang sudah tidak ada di disk dari semua file state."""
    valid = set(get_all_skills())

    disabled = [s for s in load_json(DISABLED_FILE, []) if s in valid]
    save_json(DISABLED_FILE, sorted(disabled))

    always_on = [s for s in load_json(ALWAYS_ON_FILE, []) if s in valid]
    save_json(ALWAYS_ON_FILE, sorted(always_on))

    profiles = load_json(PROFILES_FILE, DEFAULT_PROFILES.copy())
    cleaned_profiles = {
        name: [s for s in plist if s in valid]
        for name, plist in profiles.items()
    }
    save_json(PROFILES_FILE, cleaned_profiles)


def sync_repo(url: str) -> tuple[bool, str]:
    if not (url.startswith("https://") or url.startswith("http://")):
        return False, "URL harus diawali https:// atau http://"

    slug = repo_slug(url)
    target = (SKILLS_DIR / slug).resolve()
    if not target.is_relative_to(SKILLS_DIR):
        return False, "Nama repositori tidak valid."

    with git_lock:
        try:
            if (target / ".git").exists():
                res = subprocess.run(
                    ["git", "-C", str(target), "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=60, env=GIT_ENV,
                )
            else:
                res = subprocess.run(
                    ["git", "clone", "--depth", "1", "--", url, str(target)],
                    capture_output=True, text=True, timeout=60, env=GIT_ENV,
                )
            if res.returncode != 0:
                lines = (res.stderr or res.stdout).strip().splitlines()
                err = lines[-1] if lines else "Unknown git error"
                return False, f"Gagal sync {slug}: {err}"
            return True, f"Berhasil sync {slug}"
        except subprocess.TimeoutExpired:
            return False, f"Timeout saat sync {slug}"


def auto_sync_worker():
    while SYNC_INTERVAL > 0:
        time.sleep(SYNC_INTERVAL)
        with data_lock:
            repos = load_json(REPOS_FILE, [])
        for repo in repos:
            sync_repo(repo)
        with data_lock:
            cleanup_orphaned_skills()


threading.Thread(target=auto_sync_worker, daemon=True).start()


def redirect_with_msg(msg: str = "", err: bool = False):
    if not msg:
        return RedirectResponse("/", status_code=303)
    flag = "&err=1" if err else ""
    return RedirectResponse(f"/?msg={quote_plus(msg)}{flag}", status_code=303)


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "skills_count": len(get_all_skills())})


@app.get("/", response_class=HTMLResponse)
def index(msg: str = Query(default=""), err: int = Query(default=0)):
    with data_lock:
        repos = load_json(REPOS_FILE, [])
        disabled = set(load_json(DISABLED_FILE, []))
        always_on = set(load_json(ALWAYS_ON_FILE, []))
        profiles = load_json(PROFILES_FILE, DEFAULT_PROFILES.copy())
        active_profile = ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip() if ACTIVE_PROFILE_FILE.exists() else ""

    skills = get_all_skills()
    active_count = len([s for s in skills if s not in disabled])

    banner = ""
    if msg:
        cls = "banner-err" if err else "banner-ok"
        banner = f'<div class="banner {cls}">{html.escape(msg)}</div>'

    profile_cards = "".join([
        f"""<div class="profile-item {'profile-active' if name == active_profile else ''}">
            <div>
              <strong>{html.escape(name)}</strong>
              <span class="badge">{len(plist)} skill</span>
            </div>
            <div>
              <form method="post" action="/profile/apply" style="display:inline;">
                <input type="hidden" name="name" value="{html.escape(name)}"/>
                <button class="btn-on">Pakai</button>
              </form>
              <form method="post" action="/profile/save" style="display:inline;">
                <input type="hidden" name="name" value="{html.escape(name)}"/>
                <button class="btn-add" title="Simpan skill aktif saat ini ke profile ini">Update</button>
              </form>
              <form method="post" action="/profile/delete" style="display:inline;">
                <input type="hidden" name="name" value="{html.escape(name)}"/>
                <button class="btn-del">x</button>
              </form>
            </div>
          </div>""" for name, plist in profiles.items()
    ])

    repo_rows = "".join([
        f"""<tr>
            <td><code>{html.escape(r)}</code></td>
            <td style="text-align:right;white-space:nowrap;">
              <form method="post" action="/sync" style="display:inline;">
                <input type="hidden" name="url" value="{html.escape(r)}"/>
                <button class="btn-sync">Sync</button>
              </form>
              <form method="post" action="/delete" style="display:inline;">
                <input type="hidden" name="url" value="{html.escape(r)}"/>
                <button class="btn-del">Hapus</button>
              </form>
            </td>
          </tr>""" for r in repos
    ]) or "<tr><td colspan='2' style='color:#888'>Belum ada repo GitHub.</td></tr>"

    skill_rows = "".join([
        f"""<tr class="skill-row" data-name="{html.escape(s.lower())}">
            <td style="opacity:{'0.45' if s in disabled else '1'};">
              <code>{html.escape(s)}</code>
              {'<span class="badge-umum">UMUM</span>' if s in always_on else ''}
            </td>
            <td style="text-align:right;white-space:nowrap;">
              <form method="post" action="/pin" style="display:inline;">
                <input type="hidden" name="skill" value="{html.escape(s)}"/>
                <button class="{'btn-umum-on' if s in always_on else 'btn-off'}">
                  {'Unpin Umum' if s in always_on else 'Pin Umum'}
                </button>
              </form>
              <form method="post" action="/toggle" style="display:inline;">
                <input type="hidden" name="skill" value="{html.escape(s)}"/>
                <button class="{'btn-off' if s in disabled else 'btn-on'}">
                  {'Nonaktif' if s in disabled else 'Aktif'}
                </button>
              </form>
            </td>
          </tr>""" for s in skills
    ]) or "<tr><td colspan='2' style='color:#888'>Belum ada SKILL.md terdeteksi.</td></tr>"

    return f"""<!DOCTYPE html>
    <html><head><title>MCP Skills Manager</title>
    <style>
      body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
      h1, h2 {{ color: #f8fafc; }}
      .card {{ background: #1e293b; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
      .banner {{ padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-weight: 500; }}
      .banner-ok {{ background: #064e3b; border: 1px solid #10b981; color: #a7f3d0; }}
      .banner-err {{ background: #7f1d1d; border: 1px solid #ef4444; color: #fecaca; }}
      input[type=url], input[type=text], input[type=search] {{ width: 68%; padding: 9px; border-radius: 6px; border: 1px solid #334155; background: #0f172a; color: #fff; }}
      input[type=search] {{ width: 100%; box-sizing: border-box; margin: 12px 0 4px 0; }}
      button {{ padding: 7px 12px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; }}
      .btn-add {{ background: #3b82f6; color: #fff; }}
      .btn-sync, .btn-on {{ background: #10b981; color: #fff; margin-right: 4px; }}
      .btn-umum-on {{ background: #f59e0b; color: #0f172a; margin-right: 4px; }}
      .btn-off {{ background: #475569; color: #cbd5e1; margin-right: 4px; }}
      .btn-del {{ background: #ef4444; color: #fff; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
      td {{ padding: 10px 6px; border-bottom: 1px solid #334155; }}
      code {{ background: #0f172a; padding: 3px 6px; border-radius: 4px; color: #38bdf8; }}
      .badge {{ background: #334155; color: #cbd5e1; padding: 2px 7px; border-radius: 12px; font-size: 12px; margin-left: 6px; }}
      .badge-umum {{ background: #f59e0b; color: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-left: 6px; }}
      .profile-item {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; margin-bottom: 8px; }}
      .profile-active {{ border-color: #10b981; background: #064e3b33; }}
    </style></head><body>
      <h1>MCP Skills Manager</h1>
      {banner}

      <div class="card">
        <h2 style="margin-top:0;">Profile / Preset Framework</h2>
        {profile_cards}
        <form method="post" action="/profile/save" style="margin-top:12px;">
          <input type="text" name="name" placeholder="Nama profile baru (misal: Python / Go)" required />
          <button type="submit" class="btn-add">+ Simpan Skill Aktif sbg Profile</button>
        </form>
      </div>

      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <h2 style="margin:0;">Daftar Skill ({active_count}/{len(skills)} Aktif | {len(always_on)} Umum)</h2>
          <div>
            <form method="post" action="/enable-all" style="display:inline;"><button class="btn-on">Aktifkan Semua</button></form>
            <form method="post" action="/disable-all" style="display:inline;"><button class="btn-off">Nonaktifkan Semua</button></form>
          </div>
        </div>
        <input type="search" id="skillSearch" placeholder="Cari skill (ketik nama repo atau framework)..." oninput="filterSkills()" />
        <table>{skill_rows}</table>
      </div>

      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <h2 style="margin:0;">Repositori GitHub ({len(repos)})</h2>
          <form method="post" action="/sync-all"><button class="btn-sync">Sync Semua</button></form>
        </div>
        <form method="post" action="/add" style="margin-bottom:12px;">
          <input type="url" name="url" placeholder="https://github.com/frappe/skills.git" required />
          <button type="submit" class="btn-add">+ Tambah & Clone</button>
        </form>
        <table>{repo_rows}</table>
      </div>

      <script>
        function filterSkills() {{
          const q = document.getElementById('skillSearch').value.toLowerCase();
          document.querySelectorAll('.skill-row').forEach(row => {{
            row.style.display = row.dataset.name.includes(q) ? '' : 'none';
          }});
        }}
      </script>
    </body></html>"""


@app.post("/profile/save")
def save_profile(name: str = Form(...)):
    name = name.strip()
    with data_lock:
        profiles = load_json(PROFILES_FILE, DEFAULT_PROFILES.copy())
        disabled = set(load_json(DISABLED_FILE, []))
        always_on = set(load_json(ALWAYS_ON_FILE, []))
        profiles[name] = [s for s in get_all_skills() if s not in disabled and s not in always_on]
        save_json(PROFILES_FILE, profiles)
        ACTIVE_PROFILE_FILE.write_text(name, encoding="utf-8")
    return redirect_with_msg(f"Profile '{name}' diperbarui.")


@app.post("/profile/apply")
def apply_profile(name: str = Form(...)):
    with data_lock:
        profiles = load_json(PROFILES_FILE, DEFAULT_PROFILES.copy())
        always_on = set(load_json(ALWAYS_ON_FILE, []))
        selected = set(profiles.get(name, [])) | always_on
        disabled = [s for s in get_all_skills() if s not in selected]
        save_json(DISABLED_FILE, disabled)
        ACTIVE_PROFILE_FILE.write_text(name, encoding="utf-8")
    return redirect_with_msg(f"Profile '{name}' aktif.")


@app.post("/profile/delete")
def delete_profile(name: str = Form(...)):
    with data_lock:
        profiles = load_json(PROFILES_FILE, DEFAULT_PROFILES.copy())
        profiles.pop(name, None)
        save_json(PROFILES_FILE, profiles)
    return redirect_with_msg(f"Profile '{name}' dihapus.")


@app.post("/pin")
def pin_skill(skill: str = Form(...)):
    with data_lock:
        always_on = set(load_json(ALWAYS_ON_FILE, []))
        disabled = set(load_json(DISABLED_FILE, []))
        if skill in always_on:
            always_on.remove(skill)
        else:
            always_on.add(skill)
            disabled.discard(skill)
        save_json(ALWAYS_ON_FILE, sorted(list(always_on)))
        save_json(DISABLED_FILE, sorted(list(disabled)))
    return redirect_with_msg()


@app.post("/toggle")
def toggle_skill(skill: str = Form(...)):
    with data_lock:
        disabled = set(load_json(DISABLED_FILE, []))
        always_on = set(load_json(ALWAYS_ON_FILE, []))
        if skill in disabled:
            disabled.remove(skill)
        else:
            disabled.add(skill)
            always_on.discard(skill)
        save_json(DISABLED_FILE, sorted(list(disabled)))
        save_json(ALWAYS_ON_FILE, sorted(list(always_on)))
    return redirect_with_msg()


@app.post("/enable-all")
def enable_all():
    with data_lock:
        save_json(DISABLED_FILE, [])
    return redirect_with_msg("Semua skill diaktifkan.")


@app.post("/disable-all")
def disable_all():
    with data_lock:
        always_on = set(load_json(ALWAYS_ON_FILE, []))
        disabled = [s for s in get_all_skills() if s not in always_on]
        save_json(DISABLED_FILE, disabled)
    return redirect_with_msg("Semua skill non-umum dinonaktifkan.")


@app.post("/add")
def add_repo(url: str = Form(...)):
    url = url.strip()
    ok, message = sync_repo(url)
    if ok:
        with data_lock:
            repos = load_json(REPOS_FILE, [])
            if url not in repos:
                repos.append(url)
                save_json(REPOS_FILE, repos)
    return redirect_with_msg(message, err=not ok)


@app.post("/sync")
def sync_one(url: str = Form(...)):
    ok, message = sync_repo(url)
    if ok:
        with data_lock:
            cleanup_orphaned_skills()
    return redirect_with_msg(message, err=not ok)


@app.post("/sync-all")
def sync_all():
    with data_lock:
        repos = load_json(REPOS_FILE, [])
    errors = []
    for r in repos:
        ok, message = sync_repo(r)
        if not ok:
            errors.append(message)
    with data_lock:
        cleanup_orphaned_skills()
    if errors:
        return redirect_with_msg(" | ".join(errors), err=True)
    return redirect_with_msg("Semua repositori berhasil di-sync.")


@app.post("/delete")
def delete_repo(url: str = Form(...)):
    slug = repo_slug(url)
    target = (SKILLS_DIR / slug).resolve()
    with git_lock:
        if target.is_relative_to(SKILLS_DIR) and target.exists():
            shutil.rmtree(target)
    with data_lock:
        repos = [r for r in load_json(REPOS_FILE, []) if r != url]
        save_json(REPOS_FILE, repos)
        cleanup_orphaned_skills()
    return redirect_with_msg(f"Repositori {slug} dihapus dan data skill dibersihkan.")
