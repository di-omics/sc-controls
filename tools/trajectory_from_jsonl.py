"""Render a subagent JSONL log as an ordered trajectory in Markdown.
Usage: python3 -I trajectory_from_jsonl.py <log.jsonl> <out.md> [max_output_chars]
Only assistant text, tool calls and tool results are kept; thinking is dropped."""
import sys, json, pathlib
src, dst = sys.argv[1], sys.argv[2]
maxc = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
steps = []
tool_names = {}
for line in open(src, encoding='utf-8', errors='replace'):
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except Exception: continue
    t = d.get('type'); msg = d.get('message') or {}
    content = msg.get('content')
    if t == 'assistant' and isinstance(content, list):
        for b in content:
            if not isinstance(b, dict): continue
            if b.get('type') == 'text' and b.get('text', '').strip():
                steps.append(('assistant', b['text']))
            elif b.get('type') == 'tool_use':
                tool_names[b.get('id')] = b.get('name')
                inp = b.get('input') or {}
                if b.get('name') == 'Bash':
                    body = inp.get('command', '')
                elif b.get('name') in ('Write', 'Edit', 'MultiEdit'):
                    body = f"{inp.get('file_path','')}\n{inp.get('content', inp.get('new_string',''))}"
                else:
                    body = json.dumps(inp)[:maxc]
                steps.append((f"tool_use:{b.get('name')}", body))
    elif t == 'user' and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                c = b.get('content')
                if isinstance(c, list):
                    c = "\n".join(x.get('text', '') for x in c if isinstance(x, dict))
                steps.append((f"tool_result:{tool_names.get(b.get('tool_use_id'),'?')}", str(c or '')))
out = [f"# Trajectory: {pathlib.Path(src).name}", f"Steps: {len(steps)} (assistant text, tool calls, tool results; outputs cut to {maxc} chars)", ""]
n = 0
for kind, body in steps:
    if kind.startswith('tool_use'): n += 1
    cut = body if len(body) <= maxc else body[:maxc] + f"\n[... {len(body)-maxc} more chars]"
    tag = f"step {n}" if kind.startswith('tool_use') else kind
    out.append(f"## {tag}: {kind}")
    out.append("```" + ("python" if kind.endswith('Write') else "text"))
    out.append(cut)
    out.append("```"); out.append("")
pathlib.Path(dst).write_text("\n".join(out), encoding='utf-8')
print(dst, 'steps', len(steps), 'tool calls', n)
