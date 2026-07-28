"""nxre command-line interface.

    nxre rules pull      # fetch live rules -> versioned YAML (secrets redacted)
    nxre rules list      # list local desired rules
    nxre rules show ID   # show one rule
    nxre rules diff      # desired-vs-live plan, with write-class per change
    nxre rules apply     # execute the plan (SAFE auto; GUARDED needs --apply)
    nxre rules new       # scaffold a new rule (--action writeToLog | --webhook)
    nxre rules enable ID / disable ID
    nxre validate        # validate local rules against the cached manifest
    nxre serve           # run the companion service (webhook + inspection API)

Network commands hit one NX site (``--system``, default from config).
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from .client.nx_client import NxClient
from .config import Settings, load_settings
from .models.rule import NativeRule
from .rules import apply as apply_mod
from .rules import diff as diff_mod
from .rules import repo, validate

app = typer.Typer(no_args_is_help=True, help="NX Rules Engine — rules-as-code for NX Witness.")
rules_app = typer.Typer(no_args_is_help=True, help="Manage native NX event rules as code.")
app.add_typer(rules_app, name="rules")
console = Console()


def _settings() -> Settings:
    return load_settings()


def _system(settings: Settings, system: str | None) -> str:
    return system or settings.default_system


# ---------------------------------------------------------------------------
# rules pull
# ---------------------------------------------------------------------------
@rules_app.command("pull")
def rules_pull(system: str = typer.Option(None, help="NX system name (default from config).")):
    """Fetch all live rules into versioned YAML, redacting embedded secrets."""
    settings = _settings()
    sys_name = _system(settings, system)
    sys_cfg = settings.system(sys_name)

    async def _run() -> int:
        async with NxClient(sys_cfg) as client:
            return await repo.pull(client, settings, sys_name)

    count = asyncio.run(_run())
    console.print(
        f"[green]Pulled {count} rules[/] for [bold]{sys_name}[/] into "
        f"{repo.system_dir(settings, sys_name)} (secrets -> {settings.secrets_file})"
    )


# ---------------------------------------------------------------------------
# rules list / show
# ---------------------------------------------------------------------------
@rules_app.command("list")
def rules_list(system: str = typer.Option(None)):
    """List the local desired rules for a system."""
    settings = _settings()
    sys_name = _system(settings, system)
    rules = repo.load_desired_rules(settings, sys_name)
    table = Table(title=f"{sys_name}: {len(rules)} rules")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("on", justify="center")
    table.add_column("event", style="magenta")
    table.add_column("action", style="yellow")
    table.add_column("comment")
    for r in rules:
        table.add_row(
            (r.id or "")[:8], "✓" if r.enabled else "·",
            r.event_type or "?", r.action_type or "?", r.comment[:50],
        )
    console.print(table)


@rules_app.command("show")
def rules_show(rule_id: str, system: str = typer.Option(None)):
    """Show a single local rule (secrets stay redacted)."""
    settings = _settings()
    sys_name = _system(settings, system)
    path = repo.rule_path(settings, sys_name, rule_id)
    if not path.exists():
        console.print(f"[red]No rule {rule_id!r} under {sys_name}[/]")
        raise typer.Exit(1)
    console.print(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# diff / apply
# ---------------------------------------------------------------------------
def _build_plan(settings: Settings, sys_name: str, prune: bool) -> diff_mod.Plan:
    sys_cfg = settings.system(sys_name)
    desired = repo.load_desired_rules(settings, sys_name)

    async def _live() -> list[NativeRule]:
        async with NxClient(sys_cfg) as client:
            return [NativeRule.from_api(r) for r in await client.get_rules()]

    live = asyncio.run(_live())
    return diff_mod.build_plan(
        desired, live,
        system_writable=sys_cfg.writable,
        webhook_url=settings.webhook.public_url,
        prune=prune,
    )


def _print_plan(plan: diff_mod.Plan) -> None:
    if plan.is_empty():
        console.print("[green]No changes — desired state matches the server.[/]")
        return
    table = Table(title="Plan")
    table.add_column("change")
    table.add_column("class")
    table.add_column("rule")
    table.add_column("why")
    colors = {"safe": "green", "guarded": "yellow", "blocked": "red"}
    for e in plan.changes:
        wc = e.write_class.value
        table.add_row(
            e.change.value.upper(),
            f"[{colors.get(wc, 'white')}]{wc}[/]",
            (e.rule_id or "(new)")[:12],
            "; ".join(e.reasons),
        )
    console.print(table)


@rules_app.command("diff")
def rules_diff(
    system: str = typer.Option(None),
    prune: bool = typer.Option(False, help="Include deletion of live rules absent locally."),
):
    """Show the desired-vs-live plan with a write-class per change."""
    settings = _settings()
    sys_name = _system(settings, system)
    _print_plan(_build_plan(settings, sys_name, prune))


@rules_app.command("apply")
def rules_apply(
    system: str = typer.Option(None),
    apply: bool = typer.Option(False, "--apply", help="Also execute GUARDED changes."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate everything; change nothing."),
    prune: bool = typer.Option(False, help="Include deletions (still GUARDED)."),
):
    """Apply the plan. SAFE changes auto-apply; GUARDED changes need --apply."""
    settings = _settings()
    sys_name = _system(settings, system)
    sys_cfg = settings.system(sys_name)
    plan = _build_plan(settings, sys_name, prune)
    _print_plan(plan)
    if plan.is_empty():
        return

    async def _run() -> list[apply_mod.ApplyResult]:
        async with NxClient(sys_cfg) as client:
            return await apply_mod.apply_plan(
                client, plan, execute_guarded=apply, dry_run=dry_run
            )

    results = asyncio.run(_run())
    for res in results:
        color = {"applied": "green", "simulated": "cyan", "skipped": "yellow",
                 "blocked": "red", "failed": "red"}.get(res.outcome.value, "white")
        console.print(
            f"[{color}]{res.outcome.value.upper():9}[/] "
            f"{res.entry.change.value} {(res.entry.rule_id or '(new)')[:12]} — {res.detail}"
        )
    if any(r.outcome is apply_mod.Outcome.SKIPPED for r in results):
        console.print("[yellow]Some changes were GUARDED. Re-run with --apply to execute them.[/]")


# ---------------------------------------------------------------------------
# new / enable / disable
# ---------------------------------------------------------------------------
@rules_app.command("new")
def rules_new(
    system: str = typer.Option(None),
    action: str = typer.Option("writeToLog", help="Action type for the sample rule."),
    webhook: bool = typer.Option(False, "--webhook", help="Scaffold the nxre ingestion webhook rule."),
    comment: str = typer.Option("", help="Rule comment."),
):
    """Scaffold a new rule into local YAML (then `rules apply` to push it)."""
    settings = _settings()
    sys_name = _system(settings, system)
    if webhook:
        rule = repo.scaffold_webhook_rule(settings.webhook.public_url, comment or "nxre: event ingestion webhook")
    elif action == "writeToLog":
        rule = repo.scaffold_write_to_log(comment or "nxre: sample write-to-log rule")
    else:
        console.print(f"[red]No scaffold for action {action!r}. Use --webhook or --action writeToLog.[/]")
        raise typer.Exit(1)
    path = repo.write_rule(settings, sys_name, rule)
    console.print(f"[green]Scaffolded[/] {rule.action_type} rule -> {path}\nRun `nxre rules diff` then `nxre rules apply`.")


@rules_app.command("enable")
def rules_enable(rule_id: str, system: str = typer.Option(None)):
    """Enable a rule locally (SAFE — apply to push)."""
    _toggle(rule_id, system, True)


@rules_app.command("disable")
def rules_disable(rule_id: str, system: str = typer.Option(None)):
    """Disable a rule locally (SAFE — apply to push)."""
    _toggle(rule_id, system, False)


def _toggle(rule_id: str, system: str | None, enabled: bool) -> None:
    settings = _settings()
    sys_name = _system(settings, system)
    if repo.set_rule_enabled(settings, sys_name, rule_id, enabled):
        console.print(f"[green]Set enabled={enabled}[/] on {rule_id} locally. Run `nxre rules apply`.")
    else:
        console.print(f"[red]No rule {rule_id!r} under {sys_name}[/]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
@app.command("validate")
def validate_cmd(system: str = typer.Option(None)):
    """Validate local rules against the cached manifest."""
    settings = _settings()
    sys_name = _system(settings, system)
    manifest = repo.load_manifest(settings, sys_name)
    rules = repo.load_desired_rules(settings, sys_name)
    total_errors = 0
    for r in rules:
        issues = validate.validate_rule(r, manifest)
        if issues:
            console.print(f"[bold]{(r.id or '')[:8]}[/] ({r.comment[:40]})")
            for issue in issues:
                style = "red" if issue.level == "error" else "yellow"
                console.print(f"  [{style}]{issue}[/]")
            total_errors += sum(1 for i in issues if i.level == "error")
    if total_errors:
        console.print(f"[red]{total_errors} error(s).[/]")
        raise typer.Exit(1)
    console.print(f"[green]{len(rules)} rules validated OK.[/]")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------
@app.command("serve")
def serve_cmd(
    system: str = typer.Option(None),
    host: str = typer.Option(None, help="Bind host (default from config)."),
    port: int = typer.Option(None, help="Bind port (default from config)."),
):
    """Run the companion service (webhook receiver + inspection API)."""
    import uvicorn

    from .service.app import create_app

    settings = _settings()
    sys_name = _system(settings, system)
    app_obj = create_app(settings, sys_name)
    console.print(
        f"[green]nxre serving[/] for [bold]{sys_name}[/] — POST NX events to "
        f"{settings.webhook.public_url}/webhook/nx"
    )
    uvicorn.run(app_obj, host=host or settings.webhook.host, port=port or settings.webhook.port)


if __name__ == "__main__":
    app()
