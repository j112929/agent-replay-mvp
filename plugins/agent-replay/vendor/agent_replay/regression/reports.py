import html
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from ..storage import save

def write_reports(results,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    save(directory/'results.json',{'schema_version':'1.0','results':results})
    suite=ET.Element('testsuite',name='Agent regressions',tests=str(len(results)),failures=str(sum(r['verdict']=='fail' for r in results)),errors=str(sum(r['verdict']=='error' for r in results)),skipped=str(sum(r['verdict']=='inconclusive' for r in results)))
    for r in results:
        case=ET.SubElement(suite,'testcase',name=r['case_id'],time=str(r.get('duration_ms',0)/1000))
        verdict=r['verdict']
        if verdict!='pass': ET.SubElement(case,{'fail':'failure','error':'error','inconclusive':'skipped'}.get(verdict,'error'),message=r.get('reason',verdict)).text=json.dumps(r.get('assertions',[]))
    ET.ElementTree(suite).write(directory/'junit.xml',encoding='utf-8',xml_declaration=True)
    body=''.join('<section><h2>'+html.escape(r['case_id'])+' — '+html.escape(r['verdict'])+'</h2><pre>'+html.escape(json.dumps(r,indent=2,ensure_ascii=False))+'</pre></section>' for r in results)
    (directory/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Agent Regression Report</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;background:#f4f4f4;padding:20px}section{border-bottom:1px solid #ccc}</style><h1>Agent Regression Report</h1>'+body)
