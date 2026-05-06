#!/usr/bin/env python3
"""S58: Add row_hash before/after to all 5 audit_write calls. v2 — fixed multi-line awareness."""
import shutil, sys
from pathlib import Path

DRY = "--dry-run" in sys.argv
MCP = Path("/mnt/sata/Archon/services/archon/archon_mcp_server.py")
lines = MCP.read_text().splitlines(keepends=True)
if not DRY:
    shutil.copy2(MCP, MCP.with_suffix(".py.pre-s58-rowhash"))
    print("Backup created")

out = []
n = 0
i = 0
total = len(lines)

def peek(offset):
    """Look ahead at a future line."""
    j = i + offset
    return lines[j].strip() if j < total else ""

while i < total:
    L = lines[i]
    stripped = L.strip()

    # SITE 1a: Insert snapshot BEFORE the cur.execute( that starts the UPDATE session_evals
    # The pattern is: cur.execute(  [this line]  followed by "UPDATE session_evals ...
    if stripped == 'cur.execute(' and 'UPDATE session_evals SET status=' in peek(1):
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f"{indent}# S58: snapshot before mutation for row_hash\n")
        out.append(f"{indent}_snap_bef = cur.execute(\"SELECT * FROM session_evals WHERE session_id=?\", (session_id,)).fetchone()\n")
        out.append(f"{indent}_bh_sc = row_hash(dict(_snap_bef)) if _snap_bef else \"\"\n")
        out.append(L)
        n += 1; print("1a: before-snapshot added to session_close")

    # SITE 1b: session_close audit_write
    elif 'audit_write(' in L and 'tool="crl_session_close"' in L:
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f"{indent}_snap_aft = cur.execute(\"SELECT * FROM session_evals WHERE session_id=?\", (session_id,)).fetchone()\n")
        out.append(f"{indent}_ah_sc = row_hash(dict(_snap_aft)) if _snap_aft else \"\"\n")
        out.append(L.replace(
            'details={"status": "closed", "verdicts": verdicts_inserted})',
            'before_hash=_bh_sc, after_hash=_ah_sc, details={"status": "closed", "verdicts": verdicts_inserted})'
        ))
        n += 1; print("1b: hash args added to session_close audit_write")

    # SITE 2a: before profile_update — snapshot before SELECT trust_score
    elif 'SELECT trust_score FROM agent_profiles' in L and 'row = cur' in L:
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f"{indent}# S58: snapshot before mutation for row_hash\n")
        out.append(f"{indent}_snap_bef_pu = cur.execute(\"SELECT * FROM agent_profiles WHERE agent_id=?\", (agent_id,)).fetchone()\n")
        out.append(f"{indent}_bh_pu = row_hash(dict(_snap_bef_pu)) if _snap_bef_pu else \"\"\n")
        out.append(L)
        n += 1; print("2a: before-snapshot added to profile_update")

    # SITE 2b: profile_update audit_write
    elif 'audit_write(' in L and 'tool="crl_profile_update"' in L:
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f"{indent}_snap_aft_pu = cur.execute(\"SELECT * FROM agent_profiles WHERE agent_id=?\", (agent_id,)).fetchone()\n")
        out.append(f"{indent}_ah_pu = row_hash(dict(_snap_aft_pu)) if _snap_aft_pu else \"\"\n")
        out.append(L.replace(
            'details={"verdict": verdict, "old_trust": old_trust, "new_trust": new_trust, "delta": actual_delta})',
            'before_hash=_bh_pu, after_hash=_ah_pu, details={"verdict": verdict, "old_trust": old_trust, "new_trust": new_trust, "delta": actual_delta})'
        ))
        n += 1; print("2b: hash args added to profile_update audit_write")

    # SITE 3: insight_add audit_write (INSERT — no before)
    elif 'audit_write(' in L and 'tool="crl_insight_add"' in L:
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f"{indent}_snap_aft_ia = cur.execute(\"SELECT * FROM insights WHERE insight_id=?\", (insight_id,)).fetchone()\n")
        out.append(f"{indent}_ah_ia = row_hash(dict(_snap_aft_ia)) if _snap_aft_ia else \"\"\n")
        out.append(L.replace(
            'details={"category": category, "severity": severity})',
            'after_hash=_ah_ia, details={"category": category, "severity": severity})'
        ))
        n += 1; print("3: hash arg added to insight_add audit_write")

    # SITE 4a: before directive_manage mutations
    elif stripped == 'if action == "create":' and i > 0:
        ctx = "".join(lines[max(0,i-5):i])
        if "BEGIN IMMEDIATE" in ctx and "now = datetime" in ctx:
            indent = L[:len(L) - len(L.lstrip())]
            out.append(f"{indent}# S58: snapshot before mutation for row_hash\n")
            out.append(f"{indent}_snap_bef_dm = None\n")
            out.append(f'{indent}if action in ("update", "deactivate") and directive_id:\n')
            out.append(f"{indent}    _snap_bef_dm = cur.execute(\"SELECT * FROM gld_directives WHERE directive_id=?\", (directive_id,)).fetchone()\n")
            out.append(f"{indent}_bh_dm = row_hash(dict(_snap_bef_dm)) if _snap_bef_dm else \"\"\n")
            out.append(L)
            n += 1; print("4a: before-snapshot added to directive_manage")
        else:
            out.append(L)

    # SITE 4b: directive_manage audit_write
    elif 'audit_write(' in L and 'tool="crl_directive_manage"' in L:
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f'{indent}_did = result.get("directive_id", "")\n')
        out.append(f"{indent}_snap_aft_dm = cur.execute(\"SELECT * FROM gld_directives WHERE directive_id=?\", (_did,)).fetchone() if _did else None\n")
        out.append(f"{indent}_ah_dm = row_hash(dict(_snap_aft_dm)) if _snap_aft_dm else \"\"\n")
        out.append(L.replace(
            'pk=str(result.get("directive_id", "")), session_id="", details={"action": action, "priority": priority})',
            'pk=str(_did), session_id="", before_hash=_bh_dm, after_hash=_ah_dm, details={"action": action, "priority": priority})'
        ))
        n += 1; print("4b: hash args added to directive_manage audit_write")

    # SITE 5: session_open audit_write (INSERT — no before)
    elif 'audit_write(' in L and 'tool="crl_session_open"' in L:
        indent = L[:len(L) - len(L.lstrip())]
        out.append(f"{indent}_snap_aft_so = conn.execute(\"SELECT * FROM session_evals WHERE session_id=?\", (new_session_id,)).fetchone()\n")
        out.append(f"{indent}_ah_so = row_hash(dict(_snap_aft_so)) if _snap_aft_so else \"\"\n")
        out.append(L.replace(
            'details={"title": title, "action": "open"})',
            'after_hash=_ah_so, details={"title": title, "action": "open"})'
        ))
        n += 1; print("5: hash arg added to session_open audit_write")

    else:
        out.append(L)
    i += 1

print(f"\nPatches applied: {n}/8")
if DRY:
    print(f"Lines: {len(lines)} -> {len(out)} (+{len(out)-len(lines)})")
    print("[DRY RUN]")
else:
    if n == 8:
        MCP.write_text("".join(out))
        print(f"Written: {len(out)} lines")
    else:
        print(f"WARNING: Expected 8, got {n}. NOT written.")
        sys.exit(1)
