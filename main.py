# -*- coding: utf-8 -*-
"""
悬浮翻译软件 - FloatTranslator v2.1
功能：Windows平台悬浮翻译工具，支持全局快捷键唤起、实时翻译、译文转代码变量名
"""

import sys
import os
import re
import json
import time
import uuid
import requests
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QScrollArea, QTextEdit, QPushButton, QLabel, QSystemTrayIcon,
                             QMenu, QAction, QComboBox, QFrame, QFileDialog,
                             QSizeGrip)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QEvent
from PyQt5.QtGui import QIcon, QFont, QPixmap, QPainter
from pynput import keyboard


USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

HINT_TEXT = "Enter 翻译 · Shift+Enter 换行 · Ctrl+Alt+T 显隐"

# 界面语言名 -> 内部标准代码
LANG_CODES = {
    '自动检测': 'auto',
    '中文': 'zh',
    '英文': 'en',
    '日文': 'ja',
    '韩文': 'ko',
    '法文': 'fr',
    '德文': 'de',
    '俄文': 'ru',
}


class TranslationError(Exception):
    """翻译源失败时抛出，用于驱动降级链"""
    pass


# 一个汉字/假名的信息量约等于若干个拉丁字母，据此加权避免中英混排误判
CJK_WEIGHT = 2.5


def detect_language(text):
    """按字符加权占比判定主要语种，避免中英混排被误判"""
    counts = {'zh': 0.0, 'ja': 0.0, 'ko': 0.0, 'ru': 0.0, 'en': 0.0}
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:          # 平假名 / 片假名
            counts['ja'] += CJK_WEIGHT
        elif 0xAC00 <= o <= 0xD7AF:        # 韩文音节
            counts['ko'] += CJK_WEIGHT
        elif 0x4E00 <= o <= 0x9FFF:        # 汉字
            counts['zh'] += CJK_WEIGHT
        elif 0x0400 <= o <= 0x04FF:        # 西里尔字母
            counts['ru'] += 1
        elif o < 128 and ch.isalpha():     # 拉丁字母
            counts['en'] += 1

    if counts['ja'] > 0:                   # 有假名即判日文（日文常夹汉字）
        return 'ja'
    if counts['ko'] > 0:
        return 'ko'
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else 'en'


# ---------------------------------------------------------------- 翻译源实现
# 每个函数成功返回译文字符串，失败抛 TranslationError，由降级链统一处理

def _new_session():
    s = requests.Session()
    s.headers.update({'User-Agent': USER_AGENT})
    return s


def translate_transmart(text, src, dst):
    """腾讯 TranSmart - 首选源：国内直连、响应快、质量好"""
    client_key = (f"browser-chrome-119.0.0-Windows 10-"
                  f"{uuid.uuid4()}-{int(time.time() * 1000)}")
    payload = {
        "header": {"fn": "auto_translation", "client_key": client_key},
        "type": "plain",
        "model_category": "normal",
        "text_domain": "",
        "source": {"lang": src, "text_list": [text]},
        "target": {"lang": dst},
    }
    try:
        resp = requests.post('https://transmart.qq.com/api/imt', json=payload,
                             headers={'User-Agent': USER_AGENT,
                                      'Referer': 'https://transmart.qq.com/zh-CN/index',
                                      'Origin': 'https://transmart.qq.com'},
                             timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise TranslationError(str(exc))

    if (data.get('header') or {}).get('ret_code') != 'succ':
        raise TranslationError(data.get('message') or '接口返回失败')
    lines = data.get('auto_translation') or []
    if not lines:
        raise TranslationError('返回空结果')
    return '\n'.join(lines)


def translate_youdao(text, src, dst):
    """有道翻译（aidemo 端点）- 备用源，质量稳定"""
    code_map = {'auto': 'Auto', 'zh': 'zh-CHS', 'en': 'en', 'ja': 'ja',
                'ko': 'ko', 'fr': 'fr', 'de': 'de', 'ru': 'ru'}
    payload = {
        'q': text,
        'from': code_map.get(src, 'Auto'),
        'to': code_map.get(dst, 'en'),
    }
    try:
        resp = _new_session().post('https://aidemo.youdao.com/trans',
                                   data=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise TranslationError(str(exc))

    if str(data.get('errorCode')) != '0':
        raise TranslationError(f"错误码 {data.get('errorCode')}")
    lines = data.get('translation') or []
    if not lines:
        raise TranslationError('返回空结果')
    return '\n'.join(lines)


def translate_mymemory(text, src, dst):
    """MyMemory 免费接口 - 备用源（匿名有每日字数限额）"""
    if src == 'auto':
        src = detect_language(text)
    if src == dst:
        raise TranslationError('源语言与目标语言相同')
    try:
        resp = requests.get('https://api.mymemory.translated.net/get',
                            params={'q': text, 'langpair': f'{src}|{dst}'},
                            headers={'User-Agent': USER_AGENT}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise TranslationError(str(exc))

    result = (data.get('responseData') or {}).get('translatedText') or ''
    if not result:
        raise TranslationError('返回空结果')
    upper = result.upper()
    if 'INVALID' in upper or 'MYMEMORY WARNING' in upper or 'QUERY LENGTH LIMIT' in upper:
        raise TranslationError(result[:60])
    return result


def translate_google(text, src, dst):
    """谷歌翻译 - 末位兜底（国内直连通常不可达，超时设短）"""
    try:
        resp = requests.get('https://translate.googleapis.com/translate_a/single',
                            params={'client': 'gtx', 'sl': src, 'tl': dst,
                                    'dt': 't', 'q': text},
                            headers={'User-Agent': USER_AGENT}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise TranslationError(str(exc))

    if data and data[0]:
        return ''.join(seg[0] for seg in data[0] if seg[0])
    raise TranslationError('返回空结果')


# 降级顺序：国内直连优先，海外接口兜底
TRANSLATORS = [
    ('腾讯', translate_transmart),
    ('有道', translate_youdao),
    ('MyMemory', translate_mymemory),
    ('谷歌', translate_google),
]


class TranslatorWorker(QThread):
    """翻译工作线程 - 多翻译源自动降级"""
    succeeded = pyqtSignal(str, str, str)   # 译文, 来源名, 任务标记
    failed = pyqtSignal(str, str)           # 错误摘要, 任务标记

    def __init__(self, text, src='auto', dst='zh', tag='main'):
        super().__init__()
        self.text = text
        self.src = src
        self.dst = dst
        self.tag = tag

    def run(self):
        src, dst = self.src, self.dst

        # 自动检测时若判定语种与目标一致，自动切换目标，避免原样返回
        if src == 'auto':
            detected = detect_language(self.text)
            if detected == dst:
                dst = 'en' if dst != 'en' else 'zh'

        errors = []
        for name, func in TRANSLATORS:
            try:
                result = (func(self.text, src, dst) or '').strip()
                if result:
                    self.succeeded.emit(result, name, self.tag)
                    return
                errors.append(f'{name}: 空结果')
            except TranslationError as exc:
                errors.append(f'{name}: {exc}')
            except Exception as exc:
                errors.append(f'{name}: {exc}')

        self.failed.emit(' | '.join(errors), self.tag)


# ------------------------------------------------------------ 变量名生成逻辑

STOP_WORDS = {
    'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or', 'but',
    'is', 'are', 'was', 'were', 'be', 'been', 'being', 'am', 'this', 'that',
    'these', 'those', 'with', 'by', 'from', 'as', 'it', 'its', 'into', 'than',
    'then', 'so', 'if', 'we', 'you', 'i', 'he', 'she', 'they', 'them', 'his',
    'her', 'their', 'our', 'my', 'your', 'will', 'would', 'shall', 'can',
    'could', 'should', 'do', 'does', 'did', 'have', 'has', 'had', 'there',
    'here', 'please', 'about', 'over', 'up', 'out', 'per',
}

ABBREVIATIONS = {
    'address': 'addr', 'administrator': 'admin', 'application': 'app',
    'argument': 'arg', 'attribute': 'attr', 'authentication': 'auth',
    'average': 'avg', 'background': 'bg', 'boolean': 'bool', 'button': 'btn',
    'calculate': 'calc', 'category': 'cat', 'character': 'char', 'column': 'col',
    'command': 'cmd', 'configuration': 'config', 'connection': 'conn',
    'context': 'ctx', 'control': 'ctrl', 'coordinate': 'coord', 'count': 'cnt',
    'current': 'cur', 'database': 'db', 'definition': 'def', 'delete': 'del',
    'description': 'desc', 'destination': 'dest', 'development': 'dev',
    'difference': 'diff', 'dimension': 'dim', 'directory': 'dir',
    'document': 'doc', 'element': 'el', 'environment': 'env', 'error': 'err',
    'execute': 'exec', 'expression': 'expr', 'extension': 'ext',
    'function': 'func', 'generate': 'gen', 'group': 'grp', 'identifier': 'id',
    'image': 'img', 'increment': 'inc', 'index': 'idx', 'information': 'info',
    'initialize': 'init', 'instance': 'inst', 'integer': 'int',
    'interface': 'iface', 'iterator': 'iter', 'keyboard': 'kbd',
    'language': 'lang', 'length': 'len', 'level': 'lvl', 'library': 'lib',
    'manager': 'mgr', 'maximum': 'max', 'memory': 'mem', 'message': 'msg',
    'minimum': 'min', 'navigation': 'nav', 'notification': 'notif',
    'number': 'num', 'object': 'obj', 'operation': 'op', 'option': 'opt',
    'original': 'orig', 'package': 'pkg', 'parameter': 'param',
    'password': 'pwd', 'percentage': 'pct', 'performance': 'perf',
    'permission': 'perm', 'picture': 'pic', 'pointer': 'ptr',
    'position': 'pos', 'preference': 'pref', 'previous': 'prev',
    'process': 'proc', 'production': 'prod', 'property': 'prop',
    'protocol': 'proto', 'quantity': 'qty', 'random': 'rnd',
    'rectangle': 'rect', 'reference': 'ref', 'register': 'reg',
    'repository': 'repo', 'request': 'req', 'resource': 'rsc',
    'response': 'resp', 'result': 'res', 'schedule': 'sched', 'screen': 'scr',
    'second': 'sec', 'sequence': 'seq', 'server': 'srv', 'service': 'svc',
    'session': 'sess', 'source': 'src', 'specification': 'spec',
    'standard': 'std', 'statement': 'stmt', 'statistics': 'stats',
    'string': 'str', 'structure': 'struct', 'synchronize': 'sync',
    'system': 'sys', 'table': 'tbl', 'template': 'tpl', 'temporary': 'tmp',
    'terminal': 'term', 'timestamp': 'ts', 'transaction': 'txn',
    'translation': 'trans', 'utility': 'util', 'validation': 'valid',
    'value': 'val', 'variable': 'var', 'vector': 'vec', 'version': 'ver',
    'window': 'win',
}


# "number of X" / "amount of X" 这类量词短语，量词本身信息量低，直接丢弃
QUANTIFIER_BEFORE_OF = {'number', 'amount', 'list', 'set', 'group', 'kind',
                        'type', 'sort', 'piece', 'lot', 'couple'}

DIGIT_WORDS = {'0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
               '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'}


def build_variable_names(text, max_words=4):
    """把英文短语压缩成各种命名风格的变量名"""
    tokens = re.findall(r'[A-Za-z]+|\d+', text)
    if not tokens:
        return {}

    # 拆分驼峰，统一小写
    words = []
    for token in tokens:
        if token.isdigit():
            words.append(token)
        else:
            words.extend(p.lower() for p in re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+', token) or [token.lower()])

    # 丢弃 "number of" 之类的量词，让核心名词留下来
    cleaned = []
    for i, w in enumerate(words):
        if w in QUANTIFIER_BEFORE_OF and i + 1 < len(words) and words[i + 1] == 'of':
            continue
        cleaned.append(w)

    kept = [w for w in cleaned if w not in STOP_WORDS]
    if not kept:
        kept = cleaned

    # 常见长词缩写；仍过长的截断
    shortened = []
    for w in kept:
        w = ABBREVIATIONS.get(w, w)
        if len(w) > 12:
            w = w[:8]
        shortened.append(w)

    # 超长时保留前若干个修饰词 + 最后的中心词（英文中心词通常在末尾）
    if len(shortened) > max_words:
        picked = shortened[:max_words - 1] + [shortened[-1]]
    else:
        picked = shortened

    if not picked:
        return {}
    if picked[0][0].isdigit():
        picked[0] = DIGIT_WORDS.get(picked[0], 'n' + picked[0]) if len(picked[0]) == 1 else 'n' + picked[0]

    lower = [w.lower() for w in picked]
    camel = lower[0] + ''.join(w.capitalize() for w in lower[1:])
    pascal = ''.join(w.capitalize() for w in lower)
    snake = '_'.join(lower)
    constant = '_'.join(w.upper() for w in lower)
    return {
        'camelCase': camel,
        'PascalCase': pascal,
        'snake_case': snake,
        'CONSTANT_CASE': constant,
    }


def is_mostly_ascii(text):
    """判断文本是否以拉丁字母为主，可直接拿来造变量名"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_letters = sum(1 for c in letters if ord(c) < 128)
    return ascii_letters / len(letters) > 0.7


class FloatTranslator(QWidget):
    """悬浮翻译主窗口"""

    def __init__(self):
        super().__init__()
        self.dragging = False
        self.drag_position = QPoint()
        self.workers = []
        self.shortcut_listener = None
        self.is_visible = True
        self.background_image = None
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
        self.config = self.load_config()

        self.init_ui()
        self.init_tray()
        self.init_shortcut()
        self.apply_config()

    # ------------------------------------------------------------- 配置读写
    def load_config(self):
        """加载配置"""
        default_config = {
            'window': {
                'width': 420,
                'height': 380,
                'x': None,
                'y': None
            },
            'language': {
                'from': '自动检测',
                'to': '中文'
            },
            'appearance': {
                'background_image': '',
                'background_opacity': 0.25
            }
        }

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                for key in default_config:
                    if key in saved and isinstance(saved[key], dict):
                        default_config[key].update(saved[key])
            except Exception:
                pass
        return default_config

    def save_config(self):
        """保存配置"""
        self.config['window']['width'] = self.width()
        self.config['window']['height'] = self.height()
        self.config['window']['x'] = self.x()
        self.config['window']['y'] = self.y()
        self.config['language']['from'] = self.from_lang_combo.currentText()
        self.config['language']['to'] = self.to_lang_combo.currentText()

        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def apply_config(self):
        """应用配置"""
        w = self.config['window'].get('width', 420)
        h = self.config['window'].get('height', 380)
        self.resize(w, h)

        x = self.config['window'].get('x')
        y = self.config['window'].get('y')
        if x is not None and y is not None:
            self.move(x, y)
        else:
            screen = QApplication.desktop().screenGeometry()
            self.move(screen.width() - self.width() - 30, screen.height() - self.height() - 80)

        from_lang = self.config['language'].get('from', '自动检测')
        to_lang = self.config['language'].get('to', '中文')
        idx = self.from_lang_combo.findText(from_lang)
        if idx >= 0:
            self.from_lang_combo.setCurrentIndex(idx)
        idx = self.to_lang_combo.findText(to_lang)
        if idx >= 0:
            self.to_lang_combo.setCurrentIndex(idx)

        bg_image = self.config['appearance'].get('background_image', '')
        if bg_image and os.path.exists(bg_image):
            self.set_background_image(bg_image)

    # ----------------------------------------------------------------- 界面
    def init_ui(self):
        """初始化UI"""
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(350, 300)
        self.resize(420, 380)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setStyleSheet("""
            QFrame#container {
                background-color: rgba(255, 255, 255, 245);
                border-radius: 12px;
                border: 1px solid #e0e0e0;
            }
        """)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(15, 10, 15, 15)
        container_layout.setSpacing(10)

        # 标题栏
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)

        title_label = QLabel("🌐 悬浮翻译")
        title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        title_label.setStyleSheet("color: #333;")
        title_bar.addWidget(title_label)

        title_bar.addStretch()

        self.bg_btn = QPushButton("🎨")
        self.bg_btn.setFixedSize(24, 24)
        self.bg_btn.setToolTip("设置背景图片")
        self.bg_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                border-radius: 4px;
            }
        """)
        self.bg_btn.clicked.connect(self.choose_background)
        title_bar.addWidget(self.bg_btn)

        self.min_btn = QPushButton("−")
        self.min_btn.setFixedSize(24, 24)
        self.min_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #666;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #007bff;
            }
        """)
        self.min_btn.clicked.connect(self.hide_window)
        title_bar.addWidget(self.min_btn)

        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #666;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #ff4757;
            }
        """)
        self.close_btn.clicked.connect(self.hide_window)
        title_bar.addWidget(self.close_btn)

        container_layout.addLayout(title_bar)

        # 可滚动内容区（语言栏 → 输入框 → 翻译按钮 → 译文 → 变量名面板）
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        self.content_layout = QVBoxLayout(scroll_content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)

        self.scroll_area.setWidget(scroll_content)
        container_layout.addWidget(self.scroll_area, 1)

        # 语言选择栏
        lang_layout = QHBoxLayout()
        lang_layout.setSpacing(10)

        combo_style = """
            QComboBox {
                padding: 5px 10px;
                border: 1px solid #ddd;
                border-radius: 6px;
                background-color: white;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #007bff;
            }
        """

        self.from_lang_combo = QComboBox()
        self.from_lang_combo.addItems(['自动检测', '中文', '英文', '日文', '韩文', '法文', '德文', '俄文'])
        self.from_lang_combo.setStyleSheet(combo_style)
        lang_layout.addWidget(self.from_lang_combo)

        swap_btn = QPushButton("⇄")
        swap_btn.setFixedSize(30, 28)
        swap_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: none;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #007bff;
                color: white;
            }
        """)
        swap_btn.clicked.connect(self.swap_languages)
        lang_layout.addWidget(swap_btn)

        self.to_lang_combo = QComboBox()
        self.to_lang_combo.addItems(['中文', '英文', '日文', '韩文', '法文', '德文', '俄文'])
        self.to_lang_combo.setStyleSheet(combo_style)
        lang_layout.addWidget(self.to_lang_combo)

        self.content_layout.addLayout(lang_layout)

        # 输入框
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("输入要翻译的文字，按 Enter 翻译（Shift+Enter 换行）")
        self.input_text.setFont(QFont("Microsoft YaHei", 10))
        self.input_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 8px;
                background-color: rgba(255, 255, 255, 0.9);
                selection-background-color: #007bff;
            }
            QTextEdit:focus {
                border-color: #007bff;
            }
        """)
        self.input_text.setMinimumHeight(80)
        self.input_text.installEventFilter(self)
        self.content_layout.addWidget(self.input_text, 1)

        # 翻译按钮
        self.translate_btn = QPushButton("翻 译")
        self.translate_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.translate_btn.setFixedHeight(36)
        self.translate_btn.setStyleSheet("""
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
            QPushButton:pressed {
                background-color: #004085;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.translate_btn.clicked.connect(self.do_translate)
        self.content_layout.addWidget(self.translate_btn)

        # 结果显示（纯译文）
        self.result_text = QTextEdit()
        self.result_text.setPlaceholderText("翻译结果将显示在这里...")
        self.result_text.setFont(QFont("Microsoft YaHei", 10))
        self.result_text.setReadOnly(True)
        self.result_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 8px;
                background-color: rgba(248, 249, 250, 0.9);
                color: #333;
            }
        """)
        self.result_text.setMinimumHeight(80)
        self.last_translation = ""  # 纯译文，便于复制/交换
        self.content_layout.addWidget(self.result_text, 1)

        # 变量名面板（翻译完成后自动显示，点击即复制）—— 单行横排
        self.var_panel = QFrame()
        self.var_panel.setFrameShape(QFrame.NoFrame)
        self.var_panel.setVisible(False)
        var_layout = QVBoxLayout(self.var_panel)
        var_layout.setContentsMargins(0, 6, 0, 2)
        var_layout.setSpacing(4)

        var_title = QLabel("变量名（点击复制）")
        var_title.setFont(QFont("Microsoft YaHei", 9))
        var_title.setStyleSheet("color: #888;")
        var_layout.addWidget(var_title)

        self.var_btn_row = QHBoxLayout()
        self.var_btn_row.setSpacing(6)
        var_layout.addLayout(self.var_btn_row)
        self.content_layout.addWidget(self.var_panel)

        # 状态栏
        status_layout = QHBoxLayout()
        status_layout.setSpacing(6)
        self.status_label = QLabel(HINT_TEXT)
        self.status_label.setFont(QFont("Microsoft YaHei", 9))
        self.status_label.setStyleSheet("color: #999;")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        small_btn_style = """
            QPushButton {
                background-color: #f0f0f0;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                padding: 2px 10px;
                color: #666;
            }
            QPushButton:hover {
                background-color: #007bff;
                color: white;
            }
            QPushButton:disabled {
                color: #bbb;
            }
        """

        self.var_btn = QPushButton("变量名")
        self.var_btn.setFixedHeight(24)
        self.var_btn.setToolTip("把译文缩写成代码变量名")
        self.var_btn.setStyleSheet(small_btn_style)
        self.var_btn.clicked.connect(self.make_variable_name)
        status_layout.addWidget(self.var_btn)

        self.copy_btn = QPushButton("复制结果")
        self.copy_btn.setFixedHeight(24)
        self.copy_btn.setStyleSheet(small_btn_style)
        self.copy_btn.clicked.connect(self.copy_result)
        status_layout.addWidget(self.copy_btn)

        container_layout.addLayout(status_layout)

        size_grip = QSizeGrip(self.container)
        size_grip.setStyleSheet("QSizeGrip { width: 16px; height: 16px; }")
        container_layout.addWidget(size_grip, 0, Qt.AlignBottom | Qt.AlignRight)

        main_layout.addWidget(self.container)
        self.setLayout(main_layout)

    def init_tray(self):
        """初始化系统托盘"""
        self.tray_icon = QSystemTrayIcon(self)
        icon = self.style().standardIcon(QApplication.style().SP_ComputerIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()

        show_action = QAction("显示/隐藏窗口", self)
        show_action.triggered.connect(self.toggle_visibility)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        bg_action = QAction("设置背景图片", self)
        bg_action.triggered.connect(self.choose_background)
        tray_menu.addAction(bg_action)

        clear_bg_action = QAction("清除背景图片", self)
        clear_bg_action.triggered.connect(self.clear_background)
        tray_menu.addAction(clear_bg_action)

        tray_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        self.tray_icon.activated.connect(self.tray_activated)

    def tray_activated(self, reason):
        """托盘图标激活事件"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_visibility()

    def init_shortcut(self):
        """初始化全局快捷键 Ctrl+Alt+T"""
        def on_activate():
            QTimer.singleShot(0, self.toggle_visibility)

        def for_canonical(f):
            return lambda k: f(listener.canonical(k))

        hotkey = keyboard.HotKey(
            keyboard.HotKey.parse('<ctrl>+<alt>+t'),
            on_activate
        )

        listener = keyboard.Listener(
            on_press=for_canonical(hotkey.press),
            on_release=for_canonical(hotkey.release)
        )
        listener.daemon = True
        listener.start()
        self.shortcut_listener = listener

    # ------------------------------------------------------------- 翻译流程
    def get_lang_code(self, lang_name):
        """界面语言名转标准代码"""
        return LANG_CODES.get(lang_name, 'auto')

    def swap_languages(self):
        """交换源语言和目标语言"""
        from_text = self.from_lang_combo.currentText()
        to_text = self.to_lang_combo.currentText()

        if from_text == '自动检测':
            idx = self.from_lang_combo.findText(to_text)
            if idx >= 0:
                self.from_lang_combo.setCurrentIndex(idx)
            self.to_lang_combo.setCurrentIndex(0)
        else:
            idx = self.from_lang_combo.findText(to_text)
            if idx >= 0:
                self.from_lang_combo.setCurrentIndex(idx)
            idx = self.to_lang_combo.findText(from_text)
            if idx >= 0:
                self.to_lang_combo.setCurrentIndex(idx)

        input_text = self.input_text.toPlainText()
        self.input_text.setPlainText(self.last_translation or self.result_text.toPlainText())
        self.result_text.setPlainText(input_text)
        self.last_translation = input_text
        self.var_panel.setVisible(False)

    def start_worker(self, text, src, dst, tag):
        """启动一个翻译线程并持有引用，避免被提前回收"""
        worker = TranslatorWorker(text, src, dst, tag)
        worker.succeeded.connect(self.on_translation_succeeded)
        worker.failed.connect(self.on_translation_failed)
        worker.finished.connect(lambda w=worker: self.cleanup_worker(w))
        self.workers.append(worker)
        worker.start()

    def cleanup_worker(self, worker):
        """线程结束后释放引用"""
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater()

    def do_translate(self):
        """执行翻译"""
        text = self.input_text.toPlainText().strip()
        if not text:
            self.status_label.setText("请输入要翻译的文字")
            return

        src = self.get_lang_code(self.from_lang_combo.currentText())
        dst = self.get_lang_code(self.to_lang_combo.currentText())

        self.translate_btn.setEnabled(False)
        self.translate_btn.setText("翻译中...")
        self.status_label.setText("正在翻译...")
        self.start_worker(text, src, dst, 'main')

    def on_translation_succeeded(self, result, source, tag):
        """翻译成功回调"""
        if tag == 'varname':
            # 变量名专用 worker 返回的英文，更新面板
            self.update_var_panel(build_variable_names(result))
            self.set_status("已生成变量名（点击复制）", 2500)
            return

        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("翻 译")
        self.last_translation = result
        self.result_text.setPlainText(result)
        self.set_status(f"翻译完成 · 来源 {source}")
        # 翻译成功后自动生成变量名，无需手动点击
        self.auto_variable_names(result)

    def on_translation_failed(self, error, tag):
        """翻译失败回调"""
        if tag == 'varname':
            self.var_btn.setEnabled(True)
            self.var_btn.setText("变量名")
            self.set_status("变量名生成失败（转换英文出错）")
            return

        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("翻 译")
        self.result_text.setPlainText(f"所有翻译源均失败：\n{error}")
        self.set_status("翻译失败")

    def set_status(self, text, reset_after=3000):
        """更新状态栏，稍后恢复默认提示"""
        self.status_label.setText(text)
        if reset_after:
            QTimer.singleShot(reset_after, lambda: self.status_label.setText(HINT_TEXT))

    def copy_result(self):
        """复制翻译结果（仅译文，不带变量名区）"""
        result = self.last_translation or self.result_text.toPlainText()
        if result:
            QApplication.clipboard().setText(result)
            self.set_status("已复制到剪贴板", 2000)

    # ----------------------------------------------------------- 变量名功能
    def auto_variable_names(self, text):
        """翻译完成后自动把译文转成变量名并展示到面板"""
        if is_mostly_ascii(text):
            self.update_var_panel(build_variable_names(text))
            return

        # 译文非英文，先转成英文再造名
        self.status_label.setText("正在生成变量名...")
        self.start_worker(text, 'auto', 'en', 'varname')

    def make_variable_name(self):
        """手动重新生成变量名（按钮）"""
        text = self.last_translation or self.result_text.toPlainText().strip()
        if not text:
            self.status_label.setText("请先翻译一段文字")
            return
        self.auto_variable_names(text)

    def update_var_panel(self, names):
        """把变量名风格渲染成可点击按钮，单行横排填入面板"""
        # 清空旧按钮
        while self.var_btn_row.count():
            item = self.var_btn_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not names:
            self.var_panel.setVisible(False)
            return

        btn_style = """
            QPushButton {
                background-color: #f5f8ff;
                border: 1px solid #cfe2ff;
                border-radius: 5px;
                font-family: Consolas, Menlo, monospace;
                font-size: 10px;
                padding: 3px 7px;
                color: #1a4f8b;
            }
            QPushButton:hover {
                background-color: #007bff;
                color: white;
                border-color: #007bff;
            }
        """
        for style, name in names.items():
            btn = QPushButton(name)
            btn.setToolTip(f"{style} · 点击复制")
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(
                lambda _, v=name: self.copy_variable_name(v))
            self.var_btn_row.addWidget(btn)

        self.var_btn_row.addStretch()
        self.var_panel.setVisible(True)

    def copy_variable_name(self, name):
        """复制生成的变量名"""
        QApplication.clipboard().setText(name)
        self.set_status(f"已复制变量名 {name}", 2500)

    # ------------------------------------------------------------- 外观相关
    def choose_background(self):
        """选择背景图片"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if file_path:
            self.set_background_image(file_path)
            self.config['appearance']['background_image'] = file_path
            self.save_config()
            self.set_status("背景图片已设置", 2000)

    def set_background_image(self, image_path):
        """设置背景图片"""
        self.background_image = QPixmap(image_path)
        self.update()

    def clear_background(self):
        """清除背景图片"""
        self.background_image = None
        self.config['appearance']['background_image'] = ''
        self.save_config()
        self.update()
        self.set_status("背景图片已清除", 2000)

    def paintEvent(self, event):
        """绘制背景"""
        super().paintEvent(event)

        if self.background_image and not self.background_image.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

            scaled_bg = self.background_image.scaled(
                self.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation
            )

            x = (scaled_bg.width() - self.width()) // 2
            y = (scaled_bg.height() - self.height()) // 2
            cropped = scaled_bg.copy(x, y, self.width(), self.height())

            try:
                opacity = float(self.config['appearance'].get('background_opacity', 0.25))
            except (TypeError, ValueError):
                opacity = 0.25
            painter.setOpacity(max(0.05, min(0.6, opacity)))
            painter.drawPixmap(0, 0, cropped)
            painter.end()

    # ------------------------------------------------------------- 窗口行为
    def toggle_visibility(self):
        """切换窗口显示/隐藏"""
        if self.isVisible():
            self.hide_window()
        else:
            self.show_window()

    def show_window(self):
        """显示窗口"""
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_text.setFocus()
        self.is_visible = True

    def hide_window(self):
        """隐藏窗口"""
        self.save_config()
        self.hide()
        self.is_visible = False

    def quit_app(self):
        """退出应用"""
        self.save_config()
        if self.shortcut_listener:
            self.shortcut_listener.stop()
        self.tray_icon.hide()
        QApplication.quit()

    def eventFilter(self, obj, event):
        """输入框回车键处理：Enter 翻译，Shift+Enter 换行"""
        if obj is self.input_text and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    return False
                self.do_translate()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        """鼠标按下事件 - 用于拖动窗口"""
        if event.button() == Qt.LeftButton:
            if event.pos().y() < 40:
                self.dragging = True
                self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 拖动窗口"""
        if self.dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if self.dragging:
            self.dragging = False
            self.save_config()

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        if self.background_image:
            self.update()

    def closeEvent(self, event):
        """关闭事件 - 最小化到托盘"""
        event.ignore()
        self.hide_window()

    def keyPressEvent(self, event):
        """窗口级快捷键：Esc 隐藏窗口"""
        if event.key() == Qt.Key_Escape:
            self.hide_window()
        else:
            super().keyPressEvent(event)


def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app.setApplicationName("FloatTranslator")
    app.setApplicationDisplayName("悬浮翻译")

    window = FloatTranslator()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
