"""Portable JSON, escaped static HTML, and CI JUnit reports."""
from dataclasses import asdict
import html
import json
import os
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET
from ..storage import save, within
from .contracts import INFRA_FAILURES
from .metrics import aggregate
from .store import RunStore


def write_reports(run_directory, directory=None, comparison=None):
    store = RunStore(run_directory)
    run, results = store.load()
    directory = Path(directory or store.directory / 'reports').resolve()
    directory.mkdir(parents=True, exist_ok=True)
    summary = aggregate(results)
    value = {'run': run, 'summary': summary, 'results': [asdict(r) for r in results], 'comparison': comparison}
    if comparison:
        # Caller may attach release decision and detailed analysis without another report pipeline.
        value['quality_gate'] = comparison.get('quality_gate')
    from ..security.sanitizer import sanitize_artifact
    value = sanitize_artifact(value)
    save(directory / 'report.json', value)
    errors = sum(r.failure_category in INFRA_FAILURES for r in results)
    suite = ET.Element('testsuite', name=run['benchmark'], tests=str(len(results)),
        failures=str(sum(not r.success for r in results) - errors), errors=str(errors),
        time=str(sum(r.duration_ms for r in results) / 1000))
    rows = []
    def display(value):
        return 'unknown' if value is None else str(round(value, 4)) if isinstance(value, float) else str(value)
    escape = lambda value: html.escape(display(value), quote=True)
    for result in results:
        case = ET.SubElement(suite, 'testcase', name=result.task_id, classname=run['benchmark'], time=str(result.duration_ms / 1000))
        if not result.success:
            ET.SubElement(case, 'error' if result.failure_category in INFRA_FAILURES else 'failure',
                message=result.failure_category).text = json.dumps(result.metadata)
        ET.SubElement(case, 'system-out').text = result.trajectory_path
        link = ''
        if result.trajectory_path:
            target = within(store.directory, result.trajectory_path)
            link = '<a href="' + html.escape(quote(os.path.relpath(target, directory)), quote=True) + '">trajectory JSON</a>'
        columns = [result.task_id, 'PASS' if result.success else 'FAIL', result.failure_category or '—',
                   result.steps, result.duration_ms, result.tokens, result.cost_usd]
        rows.append('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in columns) + '<td>' + link + '</td></tr>')
    ET.ElementTree(suite).write(directory / 'junit.xml', encoding='utf-8', xml_declaration=True)
    tags = ''.join('<tr><td>' + escape(tag) + '</td><td>' + escape(round(s['success_rate'] * 100, 2)) +
        '%</td><td>' + str(s['pass_count']) + '/' + str(s['task_count']) + '</td></tr>' for tag, s in summary['by_tag'].items())
    regression = ''
    if comparison:
        regression = '<h2>Regression comparison</h2><pre>' + html.escape(json.dumps(comparison.get('quality_gate', {}), indent=2)) + '</pre>' + ''.join('<p>' + key + ': ' +
            str(len(comparison[key])) + ' — ' + escape(', '.join(comparison[key])) + '</p>'
            for key in ('newly_passing', 'newly_failing', 'unchanged_pass', 'unchanged_fail'))
        regression += '<pre>' + html.escape(json.dumps({'versions': comparison.get('versions'), 'failure_analysis': comparison.get('failure_analysis'), 'metrics': comparison['metrics'], 'by_tag': comparison['by_tag'],
            'added_tasks': comparison['added_tasks'], 'removed_tasks': comparison['removed_tasks'],
            'changed_tasks': comparison['changed_tasks']}, indent=2)) + '</pre>'
    distributions = summary['distributions']
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Agent benchmark report</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}pre{{white-space:pre-wrap}}a{{color:#165cba}}</style>
<h1>{escape(run['benchmark'])} benchmark</h1><p>Run {escape(run['id'])} · Agent {escape(run['agent_config']['name'])}</p>
<h2>Summary</h2><p>Success: {summary['success_rate'] * 100:.1f}% · Passed: {summary['pass_count']} · Failed: {summary['fail_count']}</p>
<p>Median latency: {escape(distributions['duration_ms']['median'])} ms · Median steps: {escape(distributions['steps']['median'])} · Cost/task: {escape(distributions['cost_usd']['mean'])} USD ({distributions['cost_usd']['count']}/{summary['task_count']} measured)</p>
{regression}<h2>By tag</h2><table><tr><th>Tag</th><th>Success rate</th><th>Passed / total</th></tr>{tags}</table>
<h2>Task results and failures</h2><table><tr><th>Task</th><th>Status</th><th>Failure category</th><th>Steps</th><th>Latency (ms)</th><th>Tokens</th><th>Cost (USD)</th><th>Trajectory</th></tr>{''.join(rows)}</table>
<p>Import trajectory JSON into the existing replay/debug UI, or use agent-replay inspect and replay. Recorded replay reproduces observations; it does not re-evaluate the task.</p></html>'''
    (directory / 'index.html').write_text(body, encoding='utf-8')
    return {name: str(directory / filename) for name, filename in
            [('json', 'report.json'), ('html', 'index.html'), ('junit', 'junit.xml')]}
