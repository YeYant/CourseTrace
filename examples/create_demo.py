"""Generate original example material. No textbook or teacher files are bundled."""
from pathlib import Path
import pymupdf

OUTPUT = Path(__file__).parent / 'CourseTrace-demo.pdf'
PAGES = [
    ('从概念回到原页', 'CourseTrace 原创示例 / 资料溯源练习', [
        '这是一份为演示检索与引用功能编写的原创资料。',
        '它不是教师课件，也不用于界定任何课程的考试范围。',
        '',
        '你可以试着搜索：非理想溶液、不可逆、引用。',
        '每个检索结果都连接到 PDF 中的一页。',
        '',
        '使用方法',
        '1. 在检索框输入一个概念。',
        '2. 打开结果，核对右侧原始页面。',
        '3. 复制引用，或导出 Markdown / JSON。',
        '',
        '出处比答案更早一步。先确认你正在看哪一份资料。',
    ]),
    ('保留概念中的限定词', '练习一 / 搜索「非理想溶液」', [
        '非理想溶液（non-ideal solution）',
        '检索时，「非」是概念的一部分，不应被自动丢掉。',
        '输入「非理想溶液」，应找到包含这个完整词组的原文。',
        '',
        '不可逆过程（irreversible process）',
        '同样，「不可逆」不能被当作「可逆」的同义表达。',
        '',
        '本示例只演示资料检索，不给出这些概念的课程定义。',
        '正式学习时，请核对自己教材中的定义、条件和推导。',
        '',
        '页码提示',
        '这是 PDF 文件中的第 2 页。底部的印刷页码是 11。',
        'CourseTrace 当前记录 PDF 页码，不自动认定印刷页码。',
    ]),
    ('一次可复核的引用', '练习二 / 引用与文件版本', [
        '引用需要告诉读者：资料名称、来源类型和具体页码。',
        'CourseTrace 还记录文件 SHA-256，帮助辨认资料版本。',
        '',
        '如果原文件更新，之前提取的文字可能已经过时。',
        '点击「检查文件更新」，随后对变化的资料更新索引。',
        '',
        '文件哈希只能帮助确认版本，不能证明内容正确。',
        '自动提取文本可能遗漏公式、图表或复杂版式中的信息。',
        '请始终对照原页，尤其是符号、单位和下标。',
        '',
        '本示例由 CourseTrace 项目原创，按项目许可证提供。',
    ]),
]


def build():
    pdf=pymupdf.open()
    for n,(title,subtitle,lines) in enumerate(PAGES):
        p=pdf.new_page(width=595,height=842)
        p.draw_rect((0,0,595,13),color=None,fill=(.145,.298,.788))
        p.insert_text((48,65),'CourseTrace',fontsize=14,fontname='hebo',color=(.145,.298,.788))
        p.insert_text((48,127),title,fontsize=26,fontname='china-s',color=(.10,.18,.31))
        p.insert_text((48,160),subtitle,fontsize=11,fontname='china-s',color=(.38,.44,.54))
        p.draw_line((48,184),(547,184),color=(.84,.88,.94),width=1)
        y=226
        for line in lines:
            p.insert_text((48,y),line,fontsize=12,fontname='china-s',color=(.15,.22,.33))
            y+=29
        p.draw_line((48,758),(547,758),color=(.84,.88,.94),width=1)
        p.insert_text((48,785),'Original example / CourseTrace 0.1',fontsize=9,color=(.38,.44,.54))
        p.insert_text((525,785),str(n+10),fontsize=11,color=(.38,.44,.54))
    pdf.set_metadata({'title':'CourseTrace 原创示例资料','author':'CourseTrace contributors','subject':'PDF source tracing demonstration'})
    pdf.save(OUTPUT,deflate=True)
    pdf.close()
    print(OUTPUT)


if __name__=='__main__':
    build()
