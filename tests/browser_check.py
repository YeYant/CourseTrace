"""End-to-end checks using an isolated library and installed Edge."""
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import pymupdf
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_library import pdf_bytes
from test_release import make_pdf
from playwright.sync_api import expect


def main():
    work=ROOT/'work';work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        temporary=Path(tmp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--port',str(port),'--data',str(temporary/'library'),'--no-browser'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        url=f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    with urlopen(url+'/api/health',timeout=1):break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,**({} if os.environ.get('COURSETRACE_BROWSER')=='chromium' else {'channel':'msedge'}))
                context=browser.new_context(viewport={'width':1440,'height':1050},device_scale_factor=1,permissions=['clipboard-read','clipboard-write'])
                page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(url)
                expect(page.get_by_text('从你的第一份资料开始')).to_be_visible()
                page.locator('#empty-demo').click()
                expect(page.locator('#result-count')).to_have_text('2 页匹配')
                expect(page.locator('#page-number')).to_have_value('2')
                expect(page.locator('#page-image')).to_have_attribute('src', re.compile('^blob:'))
                page.locator('#page-image').evaluate('(img) => img.decode()')
                assert '非理想溶液' in page.locator('#page-text').text_content()
                page.locator('#copy-citation').click()
                expect(page.locator('#toast')).to_contain_text('引用已复制')
                assert 'PDF 第 2 页' in page.evaluate('navigator.clipboard.readText()')
                with page.expect_download() as download:
                    page.locator('#export-json').click()
                exported=temporary/'citation.json';download.value.save_as(exported)
                citation=json.loads(exported.read_text('utf-8'))
                assert citation['pdf_page']==2 and citation['printed_page'] is None
                with page.expect_download() as download:
                    page.locator('#export-md').click()
                exported_md=temporary/'citation.md';download.value.save_as(exported_md)
                assert 'SHA-256' in exported_md.read_text('utf-8')
                page.locator('#next-page').click();expect(page.locator('#page-number')).to_have_value('3')
                page.locator('#prev-page').click();expect(page.locator('#page-number')).to_have_value('2')
                page.locator('#category').select_option('教材');expect(page.get_by_text('没有找到这段原文')).to_be_visible()
                page.locator('#category').select_option('');expect(page.locator('#result-count')).to_have_text('2 页匹配')

                # Import through the actual browser upload control, then replace that managed copy.
                page.locator('#import-open').click()
                page.locator('#pdf-file').set_input_files(ROOT/'examples'/'CourseTrace-demo.pdf')
                page.locator('#import-name').fill('我的示例 <测试>')
                page.locator('#import-category').select_option('教材')
                page.locator('#import-submit').click()
                expect(page.locator('#import-dialog')).not_to_be_visible()
                expect(page.locator('#source-title')).to_have_text('我的示例 <测试>')
                replacement=temporary/'replacement.pdf';replacement.write_bytes(pdf_bytes(['替换副本内容']))
                page.locator('.doc-item').filter(has=page.get_by_role('button',name='我的示例 <测试>',exact=True)).get_by_role('button',name='替换副本').click()
                page.locator('#replace-file').set_input_files(replacement)
                expect(page.locator('#toast')).to_have_text('副本与索引已更新。')

                # A tracked file changes on disk: it must stop participating until reindex succeeds.
                tracked=temporary/'tracked.pdf';tracked.write_bytes(pdf_bytes(['旧版示例概念']))
                page.locator('#import-open').click();page.locator('[name=method][value=path]').check()
                page.locator('#local-path').fill(str(tracked));page.locator('#import-name').fill('跟踪更新测试')
                page.locator('#import-submit').click();expect(page.locator('#import-dialog')).not_to_be_visible()
                expect(page.locator('#source-title')).to_have_text('跟踪更新测试')
                page.locator('#query').fill('旧版示例概念');page.locator('#search-form button').click()
                expect(page.locator('#result-count')).to_have_text('1 页匹配')
                tracked.write_bytes(pdf_bytes(['新版示例概念']))
                page.locator('#check-updates').click();expect(page.locator('#notice')).to_contain_text('已变化')
                expect(page.locator('#result-count')).to_have_text('0 页匹配')
                page.get_by_role('button',name='更新索引',exact=True).click()
                expect(page.locator('#toast')).to_have_text('索引已更新。')
                page.locator('#query').fill('新版示例概念');page.locator('#search-form button').click()
                expect(page.locator('#result-count')).to_have_text('1 页匹配')

                # Relocate a tracked file through the actual prompt and background job.
                moved=temporary/'relocated.pdf';tracked.rename(moved)
                page.once('dialog',lambda dialog:dialog.accept(str(moved)))
                page.locator('.doc-item').filter(has=page.get_by_role('button',name='跟踪更新测试',exact=True)).get_by_role('button',name='重新定位').click()
                expect(page.locator('#toast')).to_have_text('文件位置已更新。')
                # Tail matches beyond the former 600-character cutoff and manual selections.
                long_pdf=temporary/'long.pdf'
                long_pdf.write_bytes(make_pdf('target first\n'+'\n'.join(['原创测试段落，用于验证较长页面中的引用范围。']*30)+'\ntarget final'))
                page.locator('#import-open').click()
                page.locator('#pdf-file').set_input_files(long_pdf)
                page.locator('#import-name').fill('片段回归测试')
                page.locator('#import-submit').click()
                expect(page.locator('#source-title')).to_have_text('片段回归测试')
                page.locator('#query').fill('TARGET');page.locator('#search-form button').click()
                expect(page.locator('#result-count')).to_have_text('1 页匹配')
                page.locator('[data-result="0"]').click()
                expect(page.locator('#citation-selection option')).to_have_count(3)
                page.locator('#citation-selection').select_option('1')
                expect(page.locator('#citation-preview')).to_contain_text('target final')
                with page.expect_download() as download:page.locator('#export-json').click()
                download.value.save_as(exported)
                tail=json.loads(exported.read_text('utf-8'))
                assert tail['selection_start']>600 and 'target final' in tail['text_excerpt']
                page.locator('details summary').click()
                page.locator('#page-text').evaluate('(el)=>{const start=el.value.indexOf("target final");el.focus();el.setSelectionRange(start,start+12);}')
                page.locator('#use-selection').click()
                expect(page.locator('#citation-preview')).to_have_text('target final')
                with page.expect_download() as download:page.locator('#export-json').click()
                download.value.save_as(exported)
                assert json.loads(exported.read_text('utf-8'))['text_excerpt']=='target final'
                page.locator('#citation-selection').select_option('')
                with page.expect_download() as download:page.locator('#export-json').click()
                download.value.save_as(exported)
                assert json.loads(exported.read_text('utf-8'))['text_excerpt']==''

                # Cancel a real multi-page extraction, leaving no library record.
                many=temporary/'many.pdf'
                with pymupdf.open() as document:
                    for _ in range(1800):document.new_page().insert_text((30,30),'Cancellation fixture')
                    document.save(many)
                page.locator('#import-open').click();page.locator('#pdf-file').set_input_files(many)
                page.locator('#import-name').fill('取消导入测试');page.locator('#import-submit').click()
                expect(page.locator('#task-dialog')).to_be_visible()
                expect(page.locator('#task-cancel')).to_be_enabled()
                page.locator('#task-cancel').click()
                expect(page.locator('#import-error')).to_contain_text('操作已取消')
                assert not any(d['title']=='取消导入测试' for d in context.request.get(url+'/api/documents').json())
                page.locator('#import-close').click()

                # Screenshots show the distributable original demo, not temporary test documents.
                documents=context.request.get(url+'/api/documents').json()
                for doc in documents:
                    if doc['title']!='从概念回到原页 · 示例资料':
                        context.request.post(url+'/api/remove',data={'id':doc['id']},headers={'X-CourseTrace':'local'})
                page.locator('#demo-load').click();expect(page.locator('#page-number')).to_have_value('2')
                page.locator('#page-number').fill('9999');page.locator('#page-number').press('Tab')
                expect(page.locator('#page-number')).to_have_value('2')
                expect(page.locator('#page-image')).to_have_attribute('src', re.compile('^blob:'))
                page.locator('#page-image').evaluate('(img) => img.decode()')
                page.locator('#toast').wait_for(state='hidden')
                page.locator('details').evaluate('(el)=>el.open=false')
                page.screenshot(path=ROOT/'docs'/'desktop-preview.png',full_page=True)
                page.locator('#import-form').evaluate('(form)=>form.reset()')
                page.locator('#import-open').click()
                page.screenshot(path=ROOT/'docs'/'import-preview.png')
                page.locator('#import-close').click()
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
                page.screenshot(path=ROOT/'docs'/'mobile-preview.png',full_page=True)
                assert not errors, errors
                browser.close()
            print(json.dumps({'passed':True,'checks':['empty state','demo search','original page preview','clipboard','JSON export','Markdown export','page navigation','source filter','PDF upload','HTML escaping','copy replacement','tracked source changes','stale exclusion','reindex','relocate','tail match selection','manual selection','page-only citation','cancel real import','mobile overflow','no browser exceptions']},ensure_ascii=False))
        finally:
            process.terminate();process.wait(timeout=10)
            if process.stderr:
                log=process.stderr.read().decode(errors='replace')
                (work/'browser-server.log').write_text(log,encoding='utf-8')
                process.stderr.close()


if __name__=='__main__':main()
