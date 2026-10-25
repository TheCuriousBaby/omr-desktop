"""大模型辅助版面识别：把带编号标注的答题卡图和候选网格块发给视觉模型，取回语义判断。"""
import base64
import json
import re
import urllib.error
import urllib.request
from typing import List, Optional

PROMPT = """你在协助为答题卡建立识别模板。图片是一张空白答题卡，图中用彩色方框和数字编号标出了程序检测到的气泡网格块。
下面是每个块的几何信息（行数 rows、列数 cols、程序的初步判断 kind/options/direction）：
{blocks}

请逐块判断并只输出一个 JSON 对象，不要输出其他文字：
{{"blocks": [{{"id": 编号, "keep": true或false, "kind": "choice"或"digits"或"other", "options": ["A","B",...] 或 null, "direction": "horizontal"或"vertical", "order": 阅读顺序序号, "prefix": 题号前缀或null}}]}}

规则：
- keep=false 表示这不是作答用的气泡区（如装饰、页眉、定位标记误检），kind 填 other。
- kind=choice 为选择题区；options 填各选项的实际字母或文字，按选项在纸面上从左到右（横向）或从上到下（纵向）的顺序；判断题可填 ["T","F"] 或 ["对","错"]。
- kind=digits 为准考号、学号等数字填涂区，每列一位数字，从 0 到 9 排列；此时 options 为 null。
- direction 指同一道题的各个选项沿哪个方向排列。
- order 按答题顺序给出：先准考号区，再选择题区由左到右、由上到下依次编号，从 1 开始。
- prefix 通常为 null（题号用 q）。"""


class AiError(Exception):
    pass


def configured(settings) -> bool:
    return bool(
        settings.get("ai.base_url").strip()
        and settings.get("ai.api_key").strip()
        and settings.get("ai.model").strip()
    )


def _endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def image_mime(data: bytes) -> str:
    return "image/jpeg" if data[:2] == bytes([0xFF, 0xD8]) else "image/png"


def build_messages(png_bytes: bytes, blocks: List[dict]) -> list:
    summary = json.dumps(
        [
            {
                "id": index + 1,
                "rows": b["rows"],
                "cols": b["cols"],
                "kind": b["kind"],
                "options": b["options"],
                "direction": b["direction"],
            }
            for index, b in enumerate(blocks)
        ],
        ensure_ascii=False,
    )
    image = base64.b64encode(png_bytes).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(blocks=summary)},
                {"type": "image_url", "image_url": {"url": f"data:{image_mime(png_bytes)};base64,{image}"}},
            ],
        }
    ]


def call_model(settings, messages: list) -> str:
    base_url = settings.get("ai.base_url").lower()
    payload = {
        "model": settings.get("ai.model").strip(),
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2000,
    }
    thinking = settings.get("ai.thinking")
    if "dashscope" in base_url and thinking in ("on", "off"):
        payload["enable_thinking"] = thinking == "on"
    extra = settings.get("ai.extra_body").strip()
    if extra:
        try:
            extra_fields = json.loads(extra)
        except ValueError as error:
            raise AiError(f"“额外请求参数”不是有效的 JSON：{error}") from error
        if not isinstance(extra_fields, dict):
            raise AiError("“额外请求参数”必须是 JSON 对象")
        payload.update(extra_fields)
    request = urllib.request.Request(
        _endpoint(settings.get("ai.base_url")),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.get('ai.api_key').strip()}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=int(settings.get("ai.timeout"))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        hints = {
            401: "密钥无效，或密钥所属地区与接口地址不匹配，请在“设置 → 大模型辅助”检查密钥和接口地址",
            403: "没有调用权限，请确认密钥所在账号已开通该模型",
            404: "接口地址或模型名称不正确，请在“设置 → 大模型辅助”检查",
            429: "请求过于频繁或额度不足，请稍后再试",
        }
        if error.code in hints:
            raise AiError(f"{hints[error.code]}（HTTP {error.code}）") from error
        raise AiError(f"接口返回 {error.code}：{detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AiError(f"无法连接接口：{error}") from error
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise AiError("接口返回格式不正确") from error


def parse_reply(text: str) -> dict:
    """从模型回复中取出 JSON 对象，兼容被代码块包裹的情况。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AiError("模型没有返回 JSON")
    try:
        return json.loads(match.group(0))
    except ValueError as error:
        raise AiError("模型返回的 JSON 无法解析") from error


def merge_reply(detection: dict, reply: dict) -> dict:
    """把模型的逐块判断合并回检测结果；缺失的块保持原判断，并按 order 重排。"""
    judgments = {int(item["id"]): item for item in reply.get("blocks", []) if "id" in item}
    merged = []
    for index, block in enumerate(detection["blocks"], start=1):
        item = judgments.get(index)
        block = dict(block, order=index)
        if item:
            block["keep"] = item.get("keep", True)
            if item.get("kind") in ("choice", "digits", "other"):
                block["kind"] = item["kind"]
            if item.get("direction") in ("horizontal", "vertical"):
                block["direction"] = item["direction"]
            options = item.get("options")
            if isinstance(options, list) and options and all(isinstance(o, str) for o in options):
                block["options"] = options
            if isinstance(item.get("order"), (int, float)):
                block["order"] = item["order"]
            if item.get("prefix"):
                block["prefix"] = str(item["prefix"])
        merged.append(block)
    merged.sort(key=lambda b: b["order"])
    return {"bubble": detection["bubble"], "blocks": merged}


def refine(settings, png_bytes: bytes, detection: dict) -> dict:
    """调用模型修正检测结果，失败时抛出 AiError。"""
    reply = parse_reply(call_model(settings, build_messages(png_bytes, detection["blocks"])))
    return merge_reply(detection, reply)


def _tiny_png() -> bytes:
    """生成一张 32x32 的纯红 PNG，用于探测模型是否支持图片输入。"""
    import struct
    import zlib

    width = height = 32
    raw = b"".join(b"\x00" + bytes([255, 0, 0]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    signature = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"
    return signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_connection(settings) -> Optional[str]:
    """发送一条带小图的最小请求，同时验证接口配置和图片输入能力；成功返回 None，失败返回原因。"""
    messages = image_message("图片是什么颜色？只回答一个词。", [_tiny_png()])
    try:
        call_model(settings, messages)
    except AiError as error:
        if "HTTP 400" in str(error) or "接口返回 400" in str(error):
            return f"{error}。如果接口与密钥无误，请确认该模型支持图片输入。"
        return str(error)
    return None


ANSWER_PROMPT = """图片是一份标准答案，可能是文字答案表，也可能是已填涂好的答题卡。请提取每道题的正确答案。
只输出一个 JSON 对象，不要输出其他文字：{"answers": {"1": "A", "2": "BD", "3": "A|C"}}
规则：
- 键是题号（数字字符串），值是答案。
- 多选题把全部正确选项按字母顺序连写，如 BD。
- 同一题有多个都算对的答案时用竖线分隔，如 A|C。
- 判断题统一写 T（对）或 F（错），除非图中使用了其他记号。
- 看不清或没有答案的题不要输出。"""

SCORING_PROMPT = """用户用自然语言描述了答题卡的评分规则，请转换为结构化 JSON。试卷共 {count} 题，题号为 1 到 {count}。
用户描述：
{text}

只输出一个 JSON 对象，不要输出其他文字：
{{"default": {{"correct": 数字, "incorrect": 数字, "unmarked": 数字}},
  "rules": [{{"ranges": [[起始题号, 结束题号]], "correct": 数字或null, "incorrect": 数字或null, "partial": 数字或null}}]}}
规则：
- default 是没有特别说明的题目的得分：答对得分、答错得分（扣分用负数）、未作答得分。
- rules 中每条对应一段题号范围（含两端，单题写 [n, n]），只写与 default 不同的题。
- partial 表示多选题漏选（只选了正确答案的一部分，且没有选错）时的得分；没有提到漏选则为 null。
- 用户没有说明的字段填 null。分值可以是小数，如 0.5。"""


def image_message(prompt: str, images: List[bytes]) -> list:
    content = [{"type": "text", "text": prompt}]
    for image in images:
        encoded = base64.b64encode(image).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{image_mime(image)};base64,{encoded}"}})
    return [{"role": "user", "content": content}]


def extract_answers(settings, images: List[bytes]) -> dict:
    """从答案图提取答案，返回 {题号: 答案}。"""
    reply = parse_reply(call_model(settings, image_message(ANSWER_PROMPT, images)))
    answers = reply.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise AiError("模型没有识别出任何答案")
    result = {}
    for key, value in answers.items():
        if str(key).strip().isdigit() and isinstance(value, str):
            result[int(str(key).strip())] = value
    if not result:
        raise AiError("模型返回的答案格式不正确")
    return result


def parse_scoring_rules(settings, text: str, count: int) -> dict:
    """把自然语言评分规则转为 {default, rules}。"""
    prompt = SCORING_PROMPT.format(count=count, text=text.strip())
    reply = parse_reply(call_model(settings, [{"role": "user", "content": prompt}]))
    if "default" not in reply and not reply.get("rules"):
        raise AiError("模型没有解析出评分规则")
    return reply


TEXT_PROMPT = """图片是答题卡上的“{field}”栏，内容可能是手写，也可能是印刷。请只输出识别出的文字本身：
- 不要解释，不要加引号、标点或前后缀。
- 看不清或栏内为空时，输出空字符串。
{hint}"""

NAME_HINT = "- 这是人名，通常为 2 到 4 个汉字（也可能是英文名）；不要输出“姓名”两个字本身。"


def read_text(settings, image: bytes, field: str) -> str:
    """让大模型读出一张小图里的文字（姓名、班级等）。"""
    hint = NAME_HINT if ("姓名" in field or "name" in field.lower()) else ""
    prompt = TEXT_PROMPT.format(field=field, hint=hint)
    reply = call_model(settings, image_message(prompt, [image]))
    text = reply.strip().splitlines()[0].strip() if reply.strip() else ""
    return text.strip("\"'“”‘’「」 ")[:40]
