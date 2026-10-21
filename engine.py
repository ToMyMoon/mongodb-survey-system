"""Pure questionnaire validation and routing; no database or web dependencies."""
import re
from decimal import Decimal, DecimalException, ROUND_CEILING, ROUND_FLOOR

from bson import Decimal128


class BusinessError(Exception):
    def __init__(self, message, status=400, code="invalid", **details):
        super().__init__(message)
        self.message, self.status, self.code, self.details = message, status, code, details


def require(condition, message, **kwargs):
    if not condition:
        raise BusinessError(message, **kwargs)


def number(value):
    if isinstance(value, Decimal128):
        return value.to_decimal()
    require(not isinstance(value, bool) and isinstance(value, (str, int, float, Decimal)), "请输入合法数字")
    text = str(value)
    require(bool(re.fullmatch(r"-?(?:\d+(?:\.\d*)?|\.\d+)", text)), "请输入普通十进制数字，不支持科学计数法")
    try:
        result = Decimal(text)
        require(result.is_finite(), "数字必须是有限值")
        # BSON Decimal128 represents up to 34 significant digits, without silent rounding.
        Decimal128(result)
        return result
    except (DecimalException, ValueError, OverflowError) as exc:
        raise BusinessError("数字超出可精确保存的范围（最多 34 位有效数字）") from exc


def answered(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{24}", value))


def check_shape(survey):
    require(isinstance(survey.get("title"), str) and len(survey["title"]) <= 30, "标题最多 30 个字符")
    require(isinstance(survey.get("description"), str) and len(survey["description"]) <= 100, "说明最多 100 个字符")
    for key in ("allowAnonymous", "allowMultipleSubmissions"):
        require(type(survey.get(key)) is bool, f"{key} 必须是布尔值")
    questions = survey.get("questions")
    require(isinstance(questions, list), "题目必须是数组")
    ids = set()
    for q in questions:
        require(isinstance(q, dict) and valid_id(q.get("_id")), "题目标识无效")
        require(q["_id"] not in ids, "题目标识不能重复")
        ids.add(q["_id"])
        require(q.get("type") in ("single", "multiple", "text", "number"), "未知题型")
        require(isinstance(q.get("title"), str), "题目内容必须是文字")
        require(type(q.get("required")) is bool, "必答设置无效")
        require(isinstance(q.get("validation"), dict), "校验规则必须是对象")
        require(isinstance(q.get("jumpRules"), list), "跳转规则必须是数组")
        opts = q.get("options")
        require(isinstance(opts, list) and len(opts) <= 10, "每题最多 10 个选项")
        option_ids = set()
        for option in opts:
            require(isinstance(option, dict) and valid_id(option.get("_id")), "选项标识无效")
            require(option["_id"] not in option_ids, "选项标识不能重复")
            option_ids.add(option["_id"])
            require(isinstance(option.get("text"), str), "选项内容必须是文字")


def check_condition(c, q, depth=1):
    require(depth <= 5, "跳转条件最多嵌套五层")
    require(isinstance(c, dict), "跳转条件必须是对象")
    op = c.get("operator")
    require(isinstance(op, str), "条件操作符必须是字符串")
    if op in ("all", "any", "not"):
        children = c.get("conditions")
        require(isinstance(children, list) and len(children) > 0, "组合条件不能为空")
        require(op != "not" or len(children) == 1, "not 必须恰有一个直接子条件")
        for child in children:
            check_condition(child, q, depth + 1)
        return
    allowed = {"single": {"eq"}, "multiple": {"contains", "containsAny", "containsAll"},
               "number": {"eq", "gt", "gte", "lt", "lte", "between"}}
    require(op == "hasAnswer" or op in allowed.get(q["type"], set()), "条件操作符与题型不符")
    if op == "hasAnswer":
        return
    value = c.get("value")
    options = {o["_id"] for o in q["options"]}
    if q["type"] in ("single", "multiple"):
        if op in ("eq", "contains"):
            require(isinstance(value, str) and value in options, "跳转引用了不存在的选项")
        else:
            require(isinstance(value, list) and len(value) > 0 and all(isinstance(v, str) for v in value), "条件选项列表不能为空")
            require(len(set(value)) == len(value) and set(value) <= options, "条件选项重复或已失效")
    elif op == "between":
        require(isinstance(value, dict) and set(value) == {"min", "max"}, "区间需要 min 和 max")
        require(number(value["min"]) <= number(value["max"]), "跳转区间上下界无效")
    else:
        number(value)


def check_publish(survey):
    check_shape(survey)
    require(bool(survey["title"].strip()), "发布前请填写问卷标题")
    questions = survey["questions"]
    require(len(questions) > 0, "发布前至少添加一道题目")
    positions = {q["_id"]: i for i, q in enumerate(questions)}
    fields = {"single": set(), "multiple": {"minSelections", "maxSelections"},
              "text": {"minLength", "maxLength"}, "number": {"minValue", "maxValue", "integerOnly"}}
    for index, q in enumerate(questions):
        try:
            require(bool(q["title"].strip()), "请填写题目内容")
            options, v = q["options"], q["validation"]
            if q["type"] in ("single", "multiple"):
                require(2 <= len(options) <= 10, "选择题需要 2～10 个选项")
                labels = [o["text"] for o in options]
                require(all(s.strip() for s in labels) and len(set(labels)) == len(labels), "选项不能为空或重复")
            else:
                require(not options, "填空题不能配置选项")
            require(set(v) <= fields[q["type"]], "校验字段与题型不符")
            if q["type"] in ("multiple", "text"):
                low_key, high_key = ("minSelections", "maxSelections") if q["type"] == "multiple" else ("minLength", "maxLength")
                for key in (low_key, high_key):
                    require(key not in v or (type(v[key]) is int and v[key] >= 0), "数量或长度必须是非负整数")
                lo, hi = v.get(low_key, 0), v.get(high_key)
                require(hi is None or lo <= hi, "最小限制不能超过最大限制")
                if q["type"] == "multiple":
                    require(lo <= len(options) and (hi is None or hi <= len(options)), "选择数量不能超过选项总数")
                    require(not q["required"] or hi is None or hi >= 1, "必答多选必须存在非空合法答案")
            if q["type"] == "number":
                require("integerOnly" not in v or type(v["integerOnly"]) is bool, "整数限制必须是布尔值")
                lo = number(v["minValue"]) if "minValue" in v else None
                hi = number(v["maxValue"]) if "maxValue" in v else None
                require(lo is None or hi is None or lo <= hi, "数字最小值不能超过最大值")
                if v.get("integerOnly") and lo is not None and hi is not None:
                    require(lo.to_integral_value(rounding=ROUND_CEILING) <= hi.to_integral_value(rounding=ROUND_FLOOR), "此区间内不存在整数")
            require(q["type"] != "text" or not q["jumpRules"], "文本题不配置跳转")
            for rule in q["jumpRules"]:
                require(isinstance(rule, dict) and "targetQuestionId" in rule, "跳转规则缺少目标")
                target = rule["targetQuestionId"]
                require(target is None or (isinstance(target, str) and positions.get(target, -1) > index), "跳转目标不存在或不在当前题之后，请修正规则")
                check_condition(rule.get("condition"), q)
        except BusinessError as exc:
            raise BusinessError(f"第 {index + 1} 题：{exc.message}", **{"code": exc.code}) from exc


def normalize_answer(q, value):
    if not answered(value):
        require(not q["required"], "此题必须回答", questionId=q["_id"])
        return None
    v, kind = q["validation"], q["type"]
    options = {o["_id"] for o in q["options"]}
    if kind == "single":
        require(isinstance(value, str) and value in options, "请选择一个合法选项")
    elif kind == "multiple":
        require(isinstance(value, list) and all(isinstance(x, str) for x in value), "多选答案必须是选项列表")
        require(len(set(value)) == len(value) and set(value) <= options, "选项重复或不属于当前题目")
        require(len(value) >= v.get("minSelections", 0), "选择数量少于最少要求")
        require("maxSelections" not in v or len(value) <= v["maxSelections"], "选择数量超过最多要求")
        value = sorted(value)
    elif kind == "text":
        require(isinstance(value, str), "文本答案必须是字符串")
        require(len(value) >= v.get("minLength", 0), "文字少于最小长度")
        require("maxLength" not in v or len(value) <= v["maxLength"], "文字超过最大长度")
    else:
        value = number(value)
        require(not v.get("integerOnly") or value == value.to_integral_value(), "请输入整数")
        require("minValue" not in v or value >= number(v["minValue"]), "数字小于最小值")
        require("maxValue" not in v or value <= number(v["maxValue"]), "数字大于最大值")
        return Decimal128(value)
    return value


def evaluate(c, value):
    op = c["operator"]
    if op == "all":
        return all(evaluate(child, value) for child in c["conditions"])
    if op == "any":
        return any(evaluate(child, value) for child in c["conditions"])
    if op == "not":
        return not evaluate(c["conditions"][0], value)
    if op == "hasAnswer":
        return answered(value)
    if not answered(value):
        return False
    expected = c["value"]
    if isinstance(value, Decimal128):
        actual = value.to_decimal()
        if op == "between":
            return number(expected["min"]) <= actual <= number(expected["max"])
        expected = number(expected)
        return {"eq": lambda: actual == expected, "gt": lambda: actual > expected,
                "gte": lambda: actual >= expected, "lt": lambda: actual < expected,
                "lte": lambda: actual <= expected}[op]()
    if op == "eq":
        return value == expected
    if op == "contains":
        return expected in value
    if op == "containsAny":
        return bool(set(value) & set(expected))
    if op == "containsAll":
        return set(expected) <= set(value)
    raise BusinessError("未知操作符")


def next_question(questions, index, value):
    for rule in questions[index]["jumpRules"]:
        if evaluate(rule["condition"], value):
            return rule["targetQuestionId"]
    return questions[index + 1]["_id"] if index + 1 < len(questions) else None


def normalize_path(version, raw_answers):
    require(isinstance(raw_answers, list), "答案必须是数组")
    questions = version["questions"]
    positions = {q["_id"]: i for i, q in enumerate(questions)}
    submitted = {}
    for a in raw_answers:
        require(isinstance(a, dict) and isinstance(a.get("questionId"), str), "答案格式错误")
        key = a["questionId"]
        require(key in positions and key not in submitted, "题目标识不存在或重复")
        submitted[key] = a.get("value")
    result, index = [], 0
    while index < len(questions):
        q = questions[index]
        try:
            value = normalize_answer(q, submitted.get(q["_id"]))
        except BusinessError as exc:
            raise BusinessError(f"第 {index + 1} 题：{exc.message}", questionId=q["_id"]) from exc
        if value is not None:
            result.append({"questionId": q["_id"], "value": value})
        target = next_question(questions, index, value)
        if target is None:
            break
        index = positions[target]
    return result


def canonical_number(value):
    text = format(value.to_decimal(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text
