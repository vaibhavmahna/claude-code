#!/usr/bin/env python3
"""Behavioral evals: run real headless Claude Code sessions against a throwaway copy of a fixture
project with the harness installed, then grade each run with deterministic checks (no LLM judge).

Every run spends tokens. Each scenario has a hard --max-budget-usd cap, and the runner stops early
once the total budget is used. Run these when you change agent prompts, skills, hooks or models.

Usage:
  python3 evals/run_evals.py --list
  python3 evals/run_evals.py --dry-run                      set up projects, print commands, spend nothing
  python3 evals/run_evals.py --only secret-stays-secret --model haiku
  python3 evals/run_evals.py --total-budget-usd 5           run everything, stop at $5
  python3 evals/run_evals.py --model opus --budget-scale 3   caps are sized for Sonnet; scale them for a dearer model
  python3 evals/run_evals.py --trials 3                     run each scenario three times, report the pass rate
  python3 evals/run_evals.py --no-harness                   control run: same scenarios, harness not installed
  python3 evals/run_evals.py --compare A.json B.json        pass rate and dollars for two result files
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixture")
RESULTS = os.path.join(HERE, "results")


# ------------------------------------------------------------------ setup

def find_claude() -> Optional[str]:
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if found:
        return found
    for pattern in ("~/.claude/local/claude",
                    "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude",
                    "~/.cursor/extensions/anthropic.claude-code-*/resources/native-binary/claude"):
        hits = sorted(glob.glob(os.path.expanduser(pattern)))
        if hits:
            return hits[-1]
    return None


def child_env() -> Dict[str, str]:
    """A clean environment: drop this session's Claude Code variables so the child is its own session."""
    return {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE") or k == "CLAUDECODE")}


def sh(cwd: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout)


def fake_secret() -> str:
    # Assembled at runtime so no secret-looking literal lives in the repo.
    return "Pw" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(18)) + "9!"


def set_json(path: str, dotted: str, value: Any) -> None:
    with open(path) as fh:
        data = json.load(fh)
    node = data
    keys = dotted.split(".")
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)


def prepare(scenario: Dict, workdir: str, harness: bool = True, tag: str = "") -> Dict[str, str]:
    proj = os.path.join(workdir, scenario["name"] + tag)
    shutil.copytree(FIXTURE, proj)
    secret = fake_secret()
    with open(os.path.join(proj, ".env"), "w") as fh:
        fh.write(f"DB_HOST=localhost\nDB_PASSWORD={secret}\n")
    with open(os.path.join(proj, ".gitignore"), "w") as fh:
        fh.write(".env\n__pycache__/\ndist/\n")
    sh(proj, "git", "init", "-q")
    sh(proj, "git", "add", "-A")
    sh(proj, "git", "-c", "user.email=evals@example.com", "-c", "user.name=evals", "commit", "-qm", "fixture")
    if harness:
        r = sh(ROOT, sys.executable, os.path.join(ROOT, "install.py"), proj, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"install failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    for step in scenario.get("setup", []):
        if "write" in step:  # {"write": {"path": "content"}}: plain project files, with or without the harness
            for rel, content in step["write"].items():
                fp = os.path.join(proj, rel)
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, "w") as fh:
                    fh.write(content.replace("$SECRET", secret))
        elif not harness:
            continue  # the remaining steps configure the harness itself
        elif "json_set" in step:
            p, key, val = step["json_set"]
            set_json(os.path.join(proj, p), key, val)
        elif "kg_upsert" in step:
            for cmd in (("upsert", *step["kg_upsert"]), ("index",)):
                r = sh(proj, sys.executable, ".claude/tools/kg.py", *cmd)
                if r.returncode != 0:
                    raise RuntimeError(f"setup step kg.py {cmd[0]} failed: {(r.stdout + r.stderr)[-500:]}")
    sh(proj, "git", "add", "-A")
    sh(proj, "git", "-c", "user.email=evals@example.com", "-c", "user.name=evals", "commit", "-qm", "harness")
    for cmd in scenario.get("after_setup", []):  # shell commands run after the baseline commit (git hooks, remotes)
        r = subprocess.run(cmd, shell=True, cwd=proj, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"after_setup failed: {cmd}\n{(r.stdout + r.stderr)[-500:]}")
    app_lines = 0
    for dp, _, fn in os.walk(os.path.join(proj, "app")):
        for f in fn:
            if f.endswith(".py"):
                with open(os.path.join(dp, f)) as fh:
                    app_lines += sum(1 for _ in fh)
    return {"proj": proj, "$SECRET": secret, "$APP_LINES": str(app_lines)}


def snapshot(proj: str) -> Dict[str, str]:
    out = {}
    for dp, dn, fn in os.walk(proj):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
        for f in fn:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, proj)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def count_tests(proj: str, d: str) -> int:
    n = 0
    for p in glob.glob(os.path.join(proj, d, "**", "*.py"), recursive=True):
        with open(p, encoding="utf-8", errors="replace") as fh:
            n += len(re.findall(r"^\s*def test_\w*\(", fh.read(), re.M))
    return n


# ------------------------------------------------------------------ run

def command(claude: str, scenario: Dict, defaults: Dict, model: Optional[str], scale: float = 1.0) -> List[str]:
    cmd = [claude, "-p", scenario["prompt"], "--output-format", "stream-json", "--verbose",
           "--max-turns", str(scenario.get("max_turns", defaults["max_turns"])),
           "--max-budget-usd", str(round(scenario.get("budget_usd", defaults["budget_usd"]) * scale, 2)),
           "--permission-mode", "acceptEdits",
           "--allowedTools", *scenario.get("allowed_tools", defaults["allowed_tools"]),
           *scenario.get("allowed_tools_add", [])]
    if model:
        cmd += ["--model", model]
    return cmd


def parse_stream(raw: str) -> Dict[str, Any]:
    texts, tools, final = [], [], {}
    inputs: List[str] = []
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") in ("assistant", "user"):
            for block in (ev.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tools.append({"name": block.get("name"), "input": block.get("input")})
                    inputs.append(json.dumps(block.get("input"), ensure_ascii=False))
                elif block.get("type") == "tool_result":
                    c = block.get("content")
                    texts.append(c if isinstance(c, str) else json.dumps(c))
        elif ev.get("type") == "result":
            final = ev
            texts.append(ev.get("result") or "")
    agents = [str((t["input"] or {}).get("subagent_type") or "") for t in tools
              if t["name"] in ("Agent", "Task") and isinstance(t["input"], dict)]
    return {"corpus": "\n".join(texts), "tools": tools, "final": final,
            "inputs": "\n".join(inputs).replace("\\n", "\n"), "agents": [a for a in agents if a]}


# ------------------------------------------------------------------ checks

def run_check(check: Dict, ctx: Dict) -> Tuple[bool, str]:
    proj, corpus = ctx["proj"], ctx["run"]["corpus"]
    inputs, agents = ctx["run"].get("inputs", ""), ctx["run"].get("agents", [])
    sub = lambda s: ctx.get(s, s)
    (kind, arg), = check.items()
    if kind == "any":
        results = [run_check(c, ctx) for c in arg]
        return any(ok for ok, _ in results), " | ".join(d for _, d in results)
    if kind == "transcript_excludes":
        # Anywhere the model saw or wrote it: replies, tool results, and the inputs of its own tool calls.
        leaked = [s for s in arg if sub(s) in corpus or sub(s) in inputs]
        return not leaked, "value leaked into the transcript" if leaked else "not in transcript"
    if kind == "result_matches":
        reply = str(ctx["run"]["final"].get("result") or "")
        missing = [s for s in arg if not re.search(s, reply)]
        return not missing, f"final reply lacks {missing}" if missing else "found in final reply"
    if kind == "result_excludes":
        reply = str(ctx["run"]["final"].get("result") or "")
        hit = [s for s in arg if re.search(s, reply)]
        return not hit, f"final reply matched {hit}" if hit else "not in final reply"
    if kind == "tool_input_excludes":
        hit = [s for s in arg if re.search(sub(s), inputs)]
        return not hit, f"tool calls matched {hit}" if hit else "no tool call matched"
    if kind == "tool_input_contains":
        missing = [s for s in arg if not re.search(sub(s), inputs)]
        return not missing, f"no tool call matched {missing}" if missing else "found"
    if kind == "agent_sequence":
        it = iter(agents)
        missing = [a for a in arg if a not in it]  # each wanted agent must appear after the previous one
        return not missing, f"spawned {agents}; missing in order: {missing}" if missing else f"spawned {agents}"
    if kind == "agents_exclude":
        bad = [a for a in agents if a in arg]
        return not bad, f"spawned {bad}" if bad else f"spawned {agents or 'none'}"
    if kind == "path_missing":
        left = [p for p in arg if os.path.lexists(os.path.join(proj, p))]
        return not left, f"still there: {left}" if left else "gone"
    if kind == "handoff_file":
        want = ["## Goal", "## Done", "## Remaining", "## Key pointers", "## Decisions made", "## Open questions",
                "## Next first step"]
        for fp in sorted(glob.glob(os.path.join(proj, ".claude", "knowledge", "handoffs", f"{arg}-*.md"))):
            body = open(fp, encoding="utf-8", errors="replace").read()
            missing = [h for h in want if h not in body]
            if not missing:
                return True, os.path.basename(fp)
            return False, f"{os.path.basename(fp)} lacks {missing}"
        return False, f"no handoff file for {arg}"
    if kind == "new_commits":
        n = len(sh(proj, "git", "log", "--oneline", f"{ctx['base_commit']}..HEAD").stdout.split("\n")) - 1 \
            if ctx.get("base_commit") else -1
        return arg["min"] <= n <= arg["max"], f"{n} new commit(s) (allowed {arg['min']}-{arg['max']})"
    if kind == "transcript_contains":
        missing = [s for s in arg if sub(s) not in corpus]
        return not missing, f"missing {missing}" if missing else "found"
    if kind == "file_exists":
        missing = [p for p in arg if not os.path.exists(os.path.join(proj, p))]
        return not missing, f"missing {missing}" if missing else "all present"
    if kind == "unchanged":
        changed = [p for p in arg if ctx["before"].get(p) != ctx["after"].get(p)]
        return not changed, f"changed {changed}" if changed else "unchanged"
    if kind == "file_contains":
        bad = []
        for p, needle in arg.items():
            fp = os.path.join(proj, p)
            if not os.path.isfile(fp) or needle not in open(fp, encoding="utf-8", errors="replace").read():
                bad.append(p)
        return not bad, f"'{list(arg.values())}' not found in {bad}" if bad else "found"
    if kind == "new_tests":
        added = count_tests(proj, arg["dir"]) - ctx["tests_before"]
        return arg["min"] <= added <= arg["max"], f"{added} new test(s) (allowed {arg['min']}-{arg['max']})"
    if kind == "command_passes":
        r = subprocess.run(arg, shell=True, cwd=proj, capture_output=True, text=True, timeout=300,
                           env={**os.environ, "SECRET": ctx.get("$SECRET", "")})
        return r.returncode == 0, "passed" if r.returncode == 0 else (r.stdout + r.stderr)[-300:]
    if kind == "no_untracked_outside":
        out = sh(proj, "git", "ls-files", "--others", "--exclude-standard").stdout.split()
        stray = [p for p in out if not p.startswith(tuple(arg))]
        return not stray, f"leftover files {stray}" if stray else "clean"
    if kind == "doc_format_ok":
        r = sh(proj, sys.executable, ".claude/tools/doc_format.py", "check", *arg)
        return r.returncode == 0, "format ok" if r.returncode == 0 else r.stdout[-400:]
    if kind == "no_em_dash":
        bad = [p for p in arg if os.path.isfile(os.path.join(proj, p)) and "\u2014" in open(os.path.join(proj, p)).read()]
        return not bad, f"em-dash in {bad}" if bad else "none"
    if kind == "glob_nonempty":
        hits = [h for h in glob.glob(os.path.join(proj, arg)) if not h.endswith(".gitkeep")]
        return bool(hits), f"{len(hits)} file(s)"
    if kind == "kg_pending_empty":
        # Ask the harness, so ignored paths (tests, lockfiles) count as handled the way the Stop hook treats them.
        out = sh(proj, sys.executable, ".claude/tools/kg.py", "pending").stdout.strip()
        empty = out == "nothing pending"
        return empty, "pending cleared" if empty else "pending KG updates left: " + out[:200]
    return False, f"unknown check {kind}"


def harness_version() -> str:
    try:
        with open(os.path.join(ROOT, "template", "claude", "harness.json")) as fh:
            return str(json.load(fh).get("version"))
    except (OSError, ValueError):
        return "?"


def compare(path_a: str, path_b: str) -> int:
    """Pass rate and dollars for two result files, scenario by scenario."""
    def load(p: str) -> Tuple[Dict, Dict[str, Dict]]:
        with open(p) as fh:
            data = json.load(fh)
        return data, {r["name"]: r for r in data.get("results", [])}

    (da, a), (db, b) = load(path_a), load(path_b)
    label = lambda d, p: f"{os.path.basename(p)} ({'harness' if d.get('harness', True) else 'no harness'}, {d.get('model') or 'default'})"  # noqa: E731
    print(f"A = {label(da, path_a)}\nB = {label(db, path_b)}\n")
    print(f"{'scenario':<28} {'A pass':>7} {'B pass':>7} {'A $':>8} {'B $':>8} {'B - A $':>9}")

    def cell(r: Optional[Dict]) -> str:
        return "-" if r is None else f"{r.get('passed_trials', int(bool(r.get('pass'))))}/{r.get('trials', 1)}"

    tot_a = tot_b = 0.0
    for name in sorted(set(a) | set(b)):
        ra, rb = a.get(name), b.get(name)
        ca, cb = (ra or {}).get("cost_usd"), (rb or {}).get("cost_usd")
        both = ca is not None and cb is not None
        if both:
            tot_a, tot_b = tot_a + ca, tot_b + cb
        money = lambda c: "-" if c is None else f"{c:.3f}"  # noqa: E731
        print(f"{name:<28} {cell(ra):>7} {cell(rb):>7} {money(ca):>8} {money(cb):>8} {(f'{cb - ca:+.3f}' if both else '-'):>9}")
    print(f"\nTotal on scenarios in both files: A ${tot_a:.2f}, B ${tot_b:.2f}, difference ${tot_b - tot_a:+.2f}")
    return 0


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run behavioral evals for the harness")
    ap.add_argument("--only", help="comma-separated scenario names")
    ap.add_argument("--model", help="main-session model for the runs (subagents keep their own models)")
    ap.add_argument("--total-budget-usd", type=float, default=10.0, help="stop starting new scenarios past this spend")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="prepare projects and print commands; spend nothing")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway projects for inspection")
    ap.add_argument("--trials", type=int, default=1, help="runs per scenario; a scenario passes only if every run does")
    ap.add_argument("--budget-scale", type=float, default=1.0,
                    help="multiply every scenario cap, e.g. 3 when the main model is Opus (caps are sized for Sonnet)")
    ap.add_argument("--no-harness", action="store_true", help="control run without the harness installed")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"), help="compare two result files and exit")
    a = ap.parse_args(argv)
    if a.compare:
        return compare(*a.compare)

    with open(os.path.join(HERE, "scenarios.json")) as fh:
        spec = json.load(fh)
    scenarios = spec["scenarios"]
    if a.only:
        wanted = set(a.only.split(","))
        scenarios = [s for s in scenarios if s["name"] in wanted]
        unknown = wanted - {s["name"] for s in scenarios}
        if unknown:
            print(f"unknown scenario(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
    if a.list:
        for s in scenarios:
            print(f"{s['name']:<24} cap ${s.get('budget_usd', spec['defaults']['budget_usd']):<5} {s['why']}")
        return 0
    claude = find_claude()
    if not claude and not a.dry_run:
        print("Claude Code CLI not found. Set CLAUDE_BIN or put `claude` on PATH.", file=sys.stderr)
        return 2

    workdir = tempfile.mkdtemp(prefix="harness-evals-")
    harness = not a.no_harness
    results, spent, failed = [], 0.0, 0
    for s in scenarios:
        trials = []
        for n in range(1, max(1, a.trials) + 1):
            if spent >= a.total_budget_usd:
                print(f"SKIP {s['name']}: total budget ${a.total_budget_usd} reached")
                break
            ctx = prepare(s, workdir, harness, f"-{n}" if a.trials > 1 else "")
            ctx["before"] = snapshot(ctx["proj"])
            ctx["tests_before"] = count_tests(ctx["proj"], "tests")
            ctx["base_commit"] = sh(ctx["proj"], "git", "rev-parse", "HEAD").stdout.strip()
            cmd = command(claude or "claude", s, spec["defaults"], a.model, a.budget_scale)
            if a.dry_run:
                print(f"DRY  {s['name']}: {ctx['proj']}\n     {' '.join(cmd[:3])[:140]} ...")
                break
            t0 = time.time()
            try:
                p = subprocess.run(cmd, cwd=ctx["proj"], capture_output=True, text=True, env=child_env(),
                                   timeout=s.get("timeout_s", spec["defaults"]["timeout_s"]))
                raw, err = p.stdout, p.stderr
            except subprocess.TimeoutExpired as exc:
                raw, err = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timeout"
            ctx["run"] = parse_stream(raw)
            ctx["after"] = snapshot(ctx["proj"])
            final = ctx["run"]["final"]
            cost = float(final.get("total_cost_usd") or 0)
            spent += cost
            checks = [(c, *run_check(c, ctx)) for c in s["checks"]]
            # A run that hit max turns, its budget or the timeout did not finish, whatever the files look like.
            problem = "no result (timeout or crash)" if not final else \
                f"run ended with {final.get('subtype') or 'an error'}" if final.get("is_error") else ""
            ok = not problem and all(passed for _, passed, _ in checks)
            secs = round(time.time() - t0)
            label = f"{s['name']}" + (f" [{n}/{a.trials}]" if a.trials > 1 else "")
            print(f"{'PASS' if ok else 'FAIL'} {label}  ${cost:.3f}  {final.get('num_turns', '?')} turns  {secs}s")
            if problem:
                print(f"     x {problem}: {(err or raw)[-300:] if not final else ''}")
            for c, passed, detail in checks:
                if not passed:
                    print(f"     x {list(c)[0]}: {detail}")
            scrub = lambda x: json.dumps(x, ensure_ascii=False).replace(ctx["$SECRET"], "$SECRET")[:400]  # noqa: E731
            trials.append({"pass": ok, "problem": problem, "cost_usd": cost, "turns": final.get("num_turns"),
                           "seconds": secs, "model_usage": final.get("modelUsage"),
                           "checks": [{"check": list(c)[0], "pass": passed, "detail": detail} for c, passed, detail in checks],
                           "agents": ctx["run"]["agents"],
                           "tools": [{"name": t["name"], "input": scrub(t["input"])} for t in ctx["run"]["tools"]],
                           "project": ctx["proj"]})
        if trials:
            passed_n = sum(1 for tr in trials if tr["pass"])
            ok_all = passed_n == len(trials)
            failed += 0 if ok_all else 1
            results.append({"name": s["name"], "pass": ok_all, "passed_trials": passed_n, "trials": len(trials),
                            "cost_usd": round(sum(tr["cost_usd"] for tr in trials), 4),
                            "seconds": sum(tr["seconds"] for tr in trials), "runs": trials})
    if not a.dry_run:
        os.makedirs(RESULTS, exist_ok=True)
        out = os.path.join(RESULTS, time.strftime("%Y%m%d-%H%M%S") + ("" if harness else "-no-harness") + ".json")
        with open(out, "w") as fh:
            json.dump({"model": a.model, "harness": harness, "harness_version": harness_version(), "trials": a.trials,
                       "spent_usd": round(spent, 4), "results": results}, fh, indent=1)
        print(f"\n{len(results) - failed}/{len(results)} passed, ${spent:.2f} spent. Details: {out}")
    if not a.keep and not a.dry_run:
        shutil.rmtree(workdir, ignore_errors=True)
    elif a.keep or a.dry_run:
        print(f"Projects kept in {workdir}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
