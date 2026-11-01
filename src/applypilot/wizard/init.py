"""ApplyPilot first-time setup wizard.

Interactive flow that creates ~/.applypilot/ with:
  - resume.txt (and optionally resume.pdf)
  - profile.json
  - searches.yaml
  - .env (LLM API key)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    APP_DIR,
    ENV_PATH,
    FILES_DIR,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)

console = Console()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _setup_resume() -> None:
    """Prompt for resume file and copy into APP_DIR."""
    console.print(Panel("[bold]Step 1: Resume[/bold]\nPoint to your master resume file (.txt or .pdf)."))

    while True:
        path_str = Prompt.ask("Resume file path")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        suffix = src.suffix.lower()
        if suffix not in (".txt", ".pdf"):
            console.print("[red]Unsupported format.[/red] Provide a .txt or .pdf file.")
            continue

        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            console.print(f"[green]Copied to {RESUME_PATH}[/green]")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
            console.print(f"[green]Copied to {RESUME_PDF_PATH}[/green]")

            # Also ask for a plain-text version for LLM consumption
            txt_path_str = Prompt.ask(
                "Plain-text version of your resume (.txt)",
                default="",
            )
            if txt_path_str.strip():
                txt_src = Path(txt_path_str.strip().strip('"').strip("'")).expanduser().resolve()
                if txt_src.exists():
                    shutil.copy2(txt_src, RESUME_PATH)
                    console.print(f"[green]Copied to {RESUME_PATH}[/green]")
                else:
                    console.print("[yellow]File not found, skipping plain-text copy.[/yellow]")
        break


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _setup_profile() -> dict:
    """Questions de profil (FR, orienté stage) ; renvoie un dict profil imbriqué."""
    console.print(Panel("[bold]Étape 2 : Profil[/bold]\nParle-nous de toi. Ces infos alimentent le scoring, l'adaptation du CV et le remplissage des formulaires."))

    profile: dict = {}

    # -- Informations personnelles --
    console.print("\n[bold cyan]Informations personnelles[/bold cyan]")
    profile["personal"] = {
        "full_name": Prompt.ask("Nom complet"),
        "email": Prompt.ask("Adresse e-mail"),
        "phone": Prompt.ask("Téléphone", default=""),
        "city": Prompt.ask("Ville"),
        "country": Prompt.ask("Pays", default="France"),
        "linkedin_url": Prompt.ask("URL LinkedIn", default=""),
        "github_url": Prompt.ask("URL GitHub / portfolio", default=""),
        "password": Prompt.ask("Mot de passe site emploi (pour les murs de connexion en auto-apply)", password=True, default=""),
    }

    # -- Études et recherche de stage --
    console.print("\n[bold cyan]Études et recherche de stage[/bold cyan]")
    school = Prompt.ask("École / université")
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Années d'expérience pro (0 si étudiant)", default="0"),
        "education_level": Prompt.ask("Niveau d'études", default="Bac+5 (cycle ingénieur)"),
        "current_job_title": Prompt.ask("Statut / intitulé actuel", default="Étudiant ingénieur en cybersécurité"),
        "school": school,
        "target_role": Prompt.ask("Type de poste visé", default="stage en intégration IT & cybersécurité"),
    }

    # -- Détails du stage --
    console.print("\n[bold cyan]Détails du stage[/bold cyan]")
    profile["internship"] = {
        "type": Prompt.ask("Type de stage", default="Stage de fin d'études"),
        "duration": Prompt.ask("Durée", default="6 mois"),
        "start_date": Prompt.ask("Date de début souhaitée", default="avril 2027"),
        "convention_required": Confirm.ask("Convention de stage nécessaire ?", default=True),
        "convention_provider": school,
        "availability": Prompt.ask("Disponibilité (phrase libre)", default="Disponible à partir d'avril 2027, pour 6 mois"),
        "remote_preference": Prompt.ask("Préférence présentiel / télétravail", default="Sur site en France / hybride"),
    }
    profile["availability"] = {
        "earliest_start_date": profile["internship"]["start_date"],
        "available_for_full_time": "Non",
        "available_for_internship": "Oui",
    }

    # -- Compétences (séparées par des virgules) --
    console.print("\n[bold cyan]Compétences[/bold cyan] (séparées par des virgules)")
    sysres = Prompt.ask("Systèmes & réseaux (ex. Windows Server, Linux, Active Directory)", default="")
    cyber = Prompt.ask("Cybersécurité (ex. IAM, durcissement, analyse de risques)", default="")
    cloud = Prompt.ask("Cloud & identité (ex. Azure, Entra ID, M365)", default="")
    langs = Prompt.ask("Langages & scripts (ex. Python, PowerShell, Bash)", default="")
    tools = Prompt.ask("Outils (ex. Git, VMware, Wireshark)", default="")
    profile["skills_boundary"] = {
        "systemes_reseaux": [s.strip() for s in sysres.split(",") if s.strip()],
        "cybersecurite": [s.strip() for s in cyber.split(",") if s.strip()],
        "cloud_identite": [s.strip() for s in cloud.split(",") if s.strip()],
        "langages_scripts": [s.strip() for s in langs.split(",") if s.strip()],
        "outils": [s.strip() for s in tools.split(",") if s.strip()],
    }

    # -- Faits du CV (vérités préservées pour l'adaptation) --
    console.print("\n[bold cyan]Faits du CV[/bold cyan]")
    console.print("[dim]Préservés à l'identique lors de l'adaptation — l'IA ne les invente ni ne les modifie jamais.[/dim]")
    companies = Prompt.ask("Entreprises / organismes à toujours garder (virgules)", default="")
    projects = Prompt.ask("Projets à toujours garder (virgules)", default="")
    metrics = Prompt.ask("Chiffres réels à préserver (ex. '20 postes migrés, 3 sites')", default="")
    profile["resume_facts"] = {
        "preserved_companies": [s.strip() for s in companies.split(",") if s.strip()],
        "preserved_projects": [s.strip() for s in projects.split(",") if s.strip()],
        "preserved_school": school.strip(),
        "real_metrics": [s.strip() for s in metrics.split(",") if s.strip()],
    }

    # -- Autorisation de travail / divers (défauts France) --
    profile["work_authorization"] = {
        "legally_authorized_to_work": "Oui",
        "require_sponsorship": "Non",
    }
    profile["compensation"] = {
        "salary_currency": "EUR",
        "gratification_note": "Gratification de stage légale",
    }
    profile["eeo_voluntary"] = {}

    # Sauvegarde
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\n[green]Profil enregistré dans {PROFILE_PATH}[/green]")
    return profile


# ---------------------------------------------------------------------------
# Search config
# ---------------------------------------------------------------------------

def _setup_searches() -> None:
    """Generate a searches.yaml from user input."""
    console.print(Panel("[bold]Step 3: Job Search Config[/bold]\nDefine what you're looking for."))

    location = Prompt.ask("Target location (e.g. 'Remote', 'Canada', 'New York, NY')", default="Remote")
    distance_str = Prompt.ask("Search radius in miles (0 for remote-only)", default="0")
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 0

    roles_raw = Prompt.ask(
        "Target job titles (comma-separated, e.g. 'Backend Engineer, Full Stack Developer')"
    )
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # Build YAML content
    lines = [
        "# ApplyPilot search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "",
        "locations:",
        f'  - location: "{location}"',
        f"    remote: {str(distance == 0).lower()}",
        "",
        "queries:",
    ]
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------

def _setup_ai_features() -> None:
    """Ask about AI scoring/tailoring — optional LLM configuration."""
    console.print(Panel(
        "[bold]Step 4: AI Features (optional)[/bold]\n"
        "An LLM powers job scoring, resume tailoring, and cover letters.\n"
        "Without this, you can still discover and enrich jobs."
    ))

    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]applypilot init[/bold].[/dim]")
        return

    console.print("Supported providers: [bold]Gemini[/bold] (recommended, free tier), OpenAI, local (Ollama/llama.cpp)")
    provider = Prompt.ask(
        "Provider",
        choices=["gemini", "openai", "local"],
        default="gemini",
    )

    env_lines = ["# ApplyPilot configuration", ""]

    if provider == "gemini":
        api_key = Prompt.ask("Gemini API key (from aistudio.google.com)")
        model = Prompt.ask("Model", default="gemini-2.0-flash")
        env_lines.append(f"GEMINI_API_KEY={api_key}")
        env_lines.append(f"LLM_MODEL={model}")
    elif provider == "openai":
        api_key = Prompt.ask("OpenAI API key")
        model = Prompt.ask("Model", default="gpt-4o-mini")
        env_lines.append(f"OPENAI_API_KEY={api_key}")
        env_lines.append(f"LLM_MODEL={model}")
    elif provider == "local":
        url = Prompt.ask("Local LLM endpoint URL", default="http://localhost:8080/v1")
        model = Prompt.ask("Model name", default="local-model")
        env_lines.append(f"LLM_URL={url}")
        env_lines.append(f"LLM_MODEL={model}")

    env_lines.append("")
    ENV_PATH.write_text("\n".join(env_lines), encoding="utf-8")
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Auto-Apply
# ---------------------------------------------------------------------------

def _setup_auto_apply() -> None:
    """Configure autonomous job application (requires Claude Code CLI)."""
    console.print(Panel(
        "[bold]Step 5: Auto-Apply (optional)[/bold]\n"
        "ApplyPilot can autonomously fill and submit job applications\n"
        "using Claude Code as the browser agent."
    ))

    if not Confirm.ask("Enable autonomous job applications?", default=True):
        console.print("[dim]You can apply manually using the tailored resumes ApplyPilot generates.[/dim]")
        return

    # Check for Claude Code CLI
    if shutil.which("claude"):
        console.print("[green]Claude Code CLI detected.[/green]")
    else:
        console.print(
            "[yellow]Claude Code CLI not found on PATH.[/yellow]\n"
            "Install it from: [bold]https://claude.ai/code[/bold]\n"
            "Auto-apply won't work until Claude Code is installed."
        )

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        # Append to existing .env or create
        if ENV_PATH.exists():
            existing = ENV_PATH.read_text(encoding="utf-8")
            if "CAPSOLVER_API_KEY" not in existing:
                ENV_PATH.write_text(
                    existing.rstrip() + f"\nCAPSOLVER_API_KEY={capsolver_key}\n",
                    encoding="utf-8",
                )
        else:
            ENV_PATH.write_text(f"# ApplyPilot configuration\nCAPSOLVER_API_KEY={capsolver_key}\n", encoding="utf-8")
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


# ---------------------------------------------------------------------------
# Optional documents
# ---------------------------------------------------------------------------

_OPTIONAL_FILE_KEYS = [
    ("profile_photo",  "Profile photo / headshot", [".jpg", ".jpeg", ".png", ".webp"]),
    ("id_document",    "Government-issued ID scan", [".pdf", ".jpg", ".jpeg", ".png"]),
    ("passport",       "Passport scan",             [".pdf", ".jpg", ".jpeg", ".png"]),
]


def _setup_optional_files(profile: dict) -> None:
    """Optionally copy documents into ~/.applypilot/files/ and record paths in profile."""
    console.print(Panel(
        "[bold]Step 6: Optional Documents (skip if not needed)[/bold]\n"
        "Profile photo, ID, passport, certificates — some applications ask for these.\n"
        "Files are copied to [cyan]~/.applypilot/files/[/cyan] for use by the apply agent."
    ))

    if not Confirm.ask("Do you have any optional documents to add?", default=False):
        console.print("[dim]Skipped. Add files to ~/.applypilot/files/ and update profile.json later.[/dim]")
        return

    files: dict[str, str] = profile.get("files", {})
    FILES_DIR.mkdir(parents=True, exist_ok=True)

    # Known file types
    for key, label, allowed_exts in _OPTIONAL_FILE_KEYS:
        if not Confirm.ask(f"Add {label}?", default=False):
            continue
        while True:
            path_str = Prompt.ask(f"{label} file path")
            src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()
            if not src.exists():
                console.print(f"[red]File not found:[/red] {src}")
                continue
            if src.suffix.lower() not in allowed_exts:
                console.print(f"[red]Unsupported format.[/red] Use one of: {', '.join(allowed_exts)}")
                continue
            dest = FILES_DIR / f"{key}{src.suffix.lower()}"
            shutil.copy2(src, dest)
            files[key] = f"~/.applypilot/files/{dest.name}"
            console.print(f"[green]Copied to {dest}[/green]")
            break

    # Free-form: certificates / other documents
    while Confirm.ask("Add another document (certificate, portfolio, etc.)?", default=False):
        doc_label = Prompt.ask("Short label for this document (e.g. 'aws_cert', 'portfolio')")
        key = doc_label.strip().lower().replace(" ", "_")
        while True:
            path_str = Prompt.ask("File path")
            src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()
            if not src.exists():
                console.print(f"[red]File not found:[/red] {src}")
                continue
            dest = FILES_DIR / f"{key}{src.suffix.lower()}"
            shutil.copy2(src, dest)
            files[key] = f"~/.applypilot/files/{dest.name}"
            console.print(f"[green]Copied to {dest}[/green]")
            break

    if files:
        profile["files"] = files
        PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print("[green]Document paths saved to profile.json[/green]")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    """Run the full interactive setup wizard."""
    console.print()
    console.print(
        Panel.fit(
            "[bold green]ApplyPilot Setup Wizard[/bold green]\n\n"
            "This will create your configuration at:\n"
            f"  [cyan]{APP_DIR}[/cyan]\n\n"
            "You can re-run this anytime with [bold]applypilot init[/bold].",
            border_style="green",
        )
    )

    ensure_dirs()
    console.print(f"[dim]Created {APP_DIR}[/dim]\n")

    # Step 1: Resume
    _setup_resume()
    console.print()

    # Step 2: Profile
    profile = _setup_profile()
    console.print()

    # Step 3: Search config
    _setup_searches()
    console.print()

    # Step 4: AI features (optional LLM)
    _setup_ai_features()
    console.print()

    # Step 5: Auto-apply (Claude Code detection)
    _setup_auto_apply()
    console.print()

    # Step 6: Optional documents (profile photo, ID, certs)
    _setup_optional_files(profile)
    console.print()

    # Done — show tier status
    from applypilot.config import get_tier, TIER_LABELS, TIER_COMMANDS

    tier = get_tier()

    tier_lines: list[str] = []
    for t in range(1, 4):
        label = TIER_LABELS[t]
        cmds = ", ".join(f"[bold]{c}[/bold]" for c in TIER_COMMANDS[t])
        if t <= tier:
            tier_lines.append(f"  [green]✓ Tier {t} — {label}[/green]  ({cmds})")
        elif t == tier + 1:
            tier_lines.append(f"  [yellow]→ Tier {t} — {label}[/yellow]  ({cmds})")
        else:
            tier_lines.append(f"  [dim]✗ Tier {t} — {label}  ({cmds})[/dim]")

    unlock_hint = ""
    if tier == 1:
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM API key (re-run [bold]applypilot init[/bold]).[/dim]"
    elif tier == 2:
        unlock_hint = "\n[dim]To unlock Tier 3: install Claude Code CLI + Chrome.[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n"
            + "\n".join(tier_lines)
            + unlock_hint,
            border_style="green",
        )
    )
