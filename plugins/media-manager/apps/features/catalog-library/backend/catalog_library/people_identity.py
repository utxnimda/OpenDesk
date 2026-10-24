"""Provider-neutral checks for conflicting person identity evidence."""
from .people_birth_dates import normalize_birth_date


def _gender(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().casefold()
    for canonical, terms in (("male", {"男", "男性", "male", "man", "남성"}),
                             ("female", {"女", "女性", "female", "woman", "여성"}),
                             ("nonbinary", {"非二元", "非二元性别", "non-binary", "nonbinary"})):
        if value in terms:
            return canonical
    return ""


def _birth(value):
    result = normalize_birth_date({}, infobox=value.get("infobox", []))["birth_date"]
    explicit = value.get("birth_date", {})
    if isinstance(explicit, dict):
        for key, maximum in (("year", 9999), ("mon", 12), ("day", 31)):
            number = explicit.get("month" if key == "mon" and "month" in explicit else key)
            if type(number) is int and 1 <= number <= maximum:
                result[key] = number
    return result


def identity_conflict_reason(person, candidate):
    before_gender, after_gender = _gender(person.get("gender")), _gender(candidate.get("gender"))
    if before_gender and after_gender and before_gender != after_gender:
        return "性别证据冲突，不能将同名人物绑定为同一人"
    before_birth, after_birth = _birth(person), _birth(candidate)
    if any(before_birth[key] != after_birth[key] for key in before_birth.keys() & after_birth.keys()):
        return "出生日期证据冲突，不能将同名人物绑定为同一人"
    return ""
