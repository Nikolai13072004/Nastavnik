"""Summary retrieval and generation strategies."""
import math
import re

import config
from src.diagnostics import (
    add_event,
    record_chunk_groups,
    record_chunks,
    record_prompt,
    set_strategy,
)


def _summary_generation_params(summary_type):
    """Параметры поиска и генерации для разных размеров конспекта."""
    summary_type = (summary_type or "").lower()
    base_top_k = getattr(config, "SUMMARY_TOP_K", 40)

    if "крат" in summary_type:
        return {
            "top_k": 8,
            "group_size": 4,
            "chunk_tokens": 400,
            "final_tokens": 700,
        }

    if "подроб" in summary_type:
        return {
            "top_k": max(80, base_top_k * 2),
            "group_size": 6,
            # Больше бюджета на промежуточные выжимки и итог, чтобы подробный
            # конспект не выбрасывал определения/перечни (Stage 46).
            "chunk_tokens": 1500,
            "final_tokens": 6000,
        }

    return {
        "top_k": 18,
        "group_size": 4,
        "chunk_tokens": 700,
        "final_tokens": 1600,
    }

def _map_call_parallel(llm, prompts, *, max_tokens):
    """Прогнать map-шаг (по одному вызову LLM на промпт) параллельно, сохраняя
    порядок промптов (важно для связности итоговой выжимки).

    Промежуточные выжимки независимы, поэтому последовательный обход (десятки
    секунд на большом материале) превращается в ограниченно-параллельный.
    Падаем в последовательный режим при ``SUMMARY_MAP_CONCURRENCY<=1`` или одном
    промпте - тогда поведение и семантика ошибок не меняются. Первый упавший
    вызов пробрасывается (как и в старом цикле) через ``future.result()`` по
    порядку.
    """
    workers = max(1, int(getattr(config, "SUMMARY_MAP_CONCURRENCY", 4) or 1))
    if workers <= 1 or len(prompts) <= 1:
        out = []
        for prompt in prompts:
            partial = llm.call(prompt, max_tokens=max_tokens)
            if partial and partial.strip():
                out.append(partial.strip())
        return out

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(workers, len(prompts))) as pool:
        futures = [pool.submit(llm.call, prompt, max_tokens=max_tokens) for prompt in prompts]
        results = [future.result() for future in futures]  # по порядку; пробрасывает ошибку
    return [r.strip() for r in results if r and r.strip()]


def _rank_topic_chunks_for_context(chunks):
    """Для узкой темы сначала отдаём модели самые релевантные фрагменты."""
    return sorted(
        chunks,
        key=lambda chunk: (
            -float(chunk.get("score", 0) or 0),
            str(chunk.get("source_file", "")),
            int(chunk.get("chunk_id", 0) or 0),
        ),
    )

def _looks_like_noisy_section(section):
    """Отсекает служебные/битые названия разделов в диагностике и фокусировке."""
    section = str(section or "").strip()
    low = section.lower()

    if not section:
        return True

    has_real_heading_marker = "глава" in low or "§" in low

    if len(section) > 90 and not has_real_heading_marker:
        return True

    if re.search(r"\b(arpanet|rto|srtt|rttv|sts-\d|stm-\d|oc-\d|ос-\d)\b", low):
        return True

    if sum(ch.isdigit() for ch in section) >= 8 and not has_real_heading_marker:
        return True

    return False


def _looks_like_noisy_chunk_text(text):
    """Отсекает оглавления и служебные фрагменты, похожие на учебный текст."""
    text = str(text or "")
    low = text.lower()

    toc_like_lines = [
        line for line in text.splitlines()
        if re.search(r"\.{5,}\s*\d+\s*$", line)
    ]

    if len(toc_like_lines) >= 2:
        return True

    if low.count("§") >= 2 and re.search(r"\.{5,}\s*\d+", text):
        return True

    if re.search(r"глава\s+\d+\s*[.\-–—]?\s*.+\.{5,}\s*\d+", low):
        return True

    return False


def _focus_chunks_on_primary_section(chunks, min_count=3):
    """
    Для узкой темы оставляет основной найденный раздел.

    Semantic search по всему учебнику иногда добавляет слабые совпадения из
    соседних глав. Если один нормальный раздел явно доминирует, строим
    конспект по нему, а не по шуму из всей базы.
    """
    if not chunks:
        return chunks

    section_scores = {}
    section_chunks = {}

    for chunk in chunks:
        section = str(chunk.get("section", "") or "").strip()

        if _looks_like_noisy_section(section):
            continue

        score = max(float(chunk.get("score", 0) or 0), 0.0)
        section_scores[section] = section_scores.get(section, 0.0) + score + 1.0
        section_chunks.setdefault(section, []).append(chunk)

    if not section_scores:
        return chunks

    best_section = max(section_scores, key=section_scores.get)
    best_chunks = section_chunks.get(best_section, [])

    if len(best_chunks) < min_count:
        return chunks

    total_score = sum(section_scores.values())
    best_share = section_scores[best_section] / total_score if total_score else 0

    if best_share < 0.45 and len(best_chunks) < 5:
        return chunks

    return _rank_topic_chunks_for_context(best_chunks)

def _topic_anchor_patterns(topic):
    """Якоря, по которым узкая тема удерживается от соседних подразделов."""
    low = str(topic or "").lower()
    words = [
        word for word in re.findall(r"[а-яёa-z0-9]{4,}", low)
        if word not in {"тема", "период", "основные", "понятия"}
    ]
    patterns = []

    if len(words) >= 2:
        patterns.append(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b")

    for left, right in zip(words, words[1:]):
        patterns.append(r"\b" + re.escape(left) + r"\W+" + re.escape(right) + r"\b")

    if "широкополос" in low and "беспровод" in low:
        patterns.extend([
            r"\bwimax\b",
            r"\bwi\W*max\b",
            r"\b802\W*16\b",
        ])

    return patterns


def _filter_chunks_by_topic_anchors(chunks, topic, min_count=3):
    """
    Убирает соседние фрагменты внутри той же главы для узких технических тем.

    Например, тема WiMAX/802.16 находится в главе рядом с 802.11. Один только
    фильтр "Глава 4" недостаточен, поэтому держим фрагменты с точными
    тематическими якорями.
    """
    patterns = _topic_anchor_patterns(topic)

    if not patterns or not chunks:
        return chunks

    anchored = []

    for chunk in chunks:
        text = str(chunk.get("text", "") or "").lower()
        if any(re.search(pattern, text) for pattern in patterns):
            anchored.append(chunk)

    if len(anchored) < min_count:
        return chunks

    return _rank_topic_chunks_for_context(anchored)


def _focus_chunks_on_dense_chunk_window(chunks, max_gap=12, min_count=3):
    """
    Убирает дальние соседние подразделы внутри одной главы.

    Если топовые фрагменты образуют плотный диапазон chunk_id, а другой
    фрагмент находится далеко, это обычно уже соседняя тема выбранной главы.
    """
    if len(chunks) < min_count:
        return chunks

    ranked = _rank_topic_chunks_for_context(chunks)
    seed_ids = []

    for chunk in ranked[:min_count]:
        try:
            seed_ids.append(int(chunk.get("chunk_id", 0) or 0))
        except (TypeError, ValueError):
            return chunks

    if not seed_ids:
        return chunks

    center = round(sum(seed_ids) / len(seed_ids))
    focused = []

    for chunk in chunks:
        try:
            chunk_id = int(chunk.get("chunk_id", 0) or 0)
        except (TypeError, ValueError):
            focused.append(chunk)
            continue

        if abs(chunk_id - center) <= max_gap:
            focused.append(chunk)

    if len(focused) < min_count:
        return chunks

    return _rank_topic_chunks_for_context(focused)

def generate_selected_section_summary(
    kb,
    llm,
    selected_file,
    section_filter,
    topic,
    summary_type,
    *,
    workspace_id,
):
    """
    Быстрый конспект по выбранному разделу.

    Если тема не указана, берём весь выбранный раздел.
    Если тема указана, сначала ищем релевантные чанки внутри раздела:
    большие главы нельзя отдавать модели целиком и надеяться на фильтрацию.
    """
    params = _summary_generation_params(summary_type)
    set_strategy("selected_section_topic" if topic else "selected_section_full")

    file_filter = "all" if selected_file == "Все файлы" else selected_file

    if topic:
        chunks = kb.search_chunks_for_summary(
            query=topic,
            file_filter=file_filter,
            section_filter=section_filter,
            top_k=params["top_k"],
            workspace_id=workspace_id,
        )
        chunks = _rank_topic_chunks_for_context(chunks)
        chunks = _filter_chunks_by_topic_anchors(chunks, topic)
        chunks = _focus_chunks_on_primary_section(chunks)
        chunks = _focus_chunks_on_dense_chunk_window(chunks)
        record_chunks("selected_section_topic_chunks", chunks)
    else:
        chunks = kb.get_file_chunks(
            file_filter=file_filter,
            section_filter=section_filter,
            workspace_id=workspace_id,
        )
        record_chunks("selected_section_full_chunks", chunks)

    if not chunks:
        return (
            "Информация по выбранному разделу не найдена.\n\n"
            "Попробуйте:\n"
            "• выбрать другой раздел;\n"
            "• выбрать «Все разделы»;\n"
            "• переиндексировать материал."
        )

    # Stage 7f: the metadata header (Материал / Раздел / Тип / Найдено
    # фрагментов / Разделы источников) was dropped from user-facing output —
    # the UI already shows this context, and it read as technical noise on top
    # of every конспект. Body only now; the header stays as an empty prefix so
    # the existing `return header + body` sites are untouched.
    header = ""

    if topic and "сред" in str(summary_type or "").lower():
        compact_context = _chunks_to_context(chunks[:8])
        prompt = f"""{config.SYSTEM_PROMPT}

ТЕМА:
{topic}

ВЫБРАННЫЙ РАЗДЕЛ:
{section_filter}

НАЙДЕННЫЕ ФРАГМЕНТЫ:
{compact_context}

Задача:
Составь средний конспект только по указанной теме внутри выбранного раздела.

Инструкция:
1. Используй только найденные фрагменты.
2. Не включай соседние темы выбранной главы, если они не раскрывают тему.
3. Структурируй материал по смысловым подпунктам.
4. Сохрани важные формулы, определения, ограничения и примеры.
5. Не добавляй факты от себя.
6. Если во фрагментах есть формулы, перепиши их явно и поясни обозначения.
7. Не добавляй дисклеймеры вида "контекст неполный", если найденные фрагменты раскрывают тему хотя бы частично.
8. В конце добавь короткий итог.

СРЕДНИЙ КОНСПЕКТ:"""

        record_prompt("selected_section_medium", prompt)
        result = llm.call(prompt, max_tokens=params["final_tokens"])
        return header + result

    group_size = params["group_size"]
    partial_summaries = []

    for i in range(0, len(chunks), group_size):
        group = chunks[i:i + group_size]

        context_parts = []

        for chunk in group:
            section = chunk.get("section", "")
            label = (
                f'{chunk["source_file"]} | {section}'
                if section
                else chunk["source_file"]
            )
            context_parts.append(f"[{label}]\n{chunk['text']}")

        context = "\n\n---\n\n".join(context_parts)

        if topic:
            prompt = config.PROMPTS["topic_summary_chunk"].format(
                system=config.SYSTEM_PROMPT,
                topic=topic,
                context=context,
            )
        else:
            prompt = config.PROMPTS["summary_chunk"].format(
                system=config.SYSTEM_PROMPT,
                context=context,
            )

        partial = llm.call(prompt, max_tokens=params["chunk_tokens"]).strip()

        if partial and not partial.upper().startswith("НЕ ОТНОСИТСЯ"):
            partial_summaries.append(partial)

    if not partial_summaries:
        return (
            "Информация по указанной теме не найдена в выбранном разделе.\n\n"
            "Попробуйте:\n"
            "• убрать тему и сделать конспект всего раздела;\n"
            "• выбрать другой раздел;\n"
            "• выбрать «Все разделы»."
        )

    combined_context = "\n\n---\n\n".join(partial_summaries)

    if topic:
        final_prompt = config.PROMPTS["topic_summary_reduce"].format(
            system=config.SYSTEM_PROMPT,
            topic=topic,
            context=combined_context,
            summary_type=summary_type.lower(),
        )
    else:
        final_prompt = config.PROMPTS["summary_reduce"].format(
            system=config.SYSTEM_PROMPT,
            context=combined_context,
            summary_type=summary_type.lower(),
        )

    result = llm.call(final_prompt, max_tokens=params["final_tokens"])

    return header + result
def generate_direct_topic_summary(
    kb,
    llm,
    topic,
    summary_type,
    file_filter="all",
    section_filter=None,
    *,
    workspace_id,
):
    """
    Быстрый тематический конспект для узких тем.

    В отличие от планового режима не расширяет запрос в общие подпункты,
    поэтому темы вроде "Широкополосные беспроводные сети" не уезжают
    в соседние разделы про все беспроводные технологии сразу.
    """
    params = _summary_generation_params(summary_type)
    set_strategy("direct_topic")
    chunks = kb.search_chunks_for_summary(
        query=topic,
        file_filter=file_filter,
        section_filter=section_filter,
        top_k=params["top_k"],
        workspace_id=workspace_id,
    )
    chunks = _rank_topic_chunks_for_context(chunks)
    chunks = _focus_chunks_on_primary_section(chunks)
    chunks = _filter_chunks_by_topic_anchors(chunks, topic)
    chunks = _focus_chunks_on_dense_chunk_window(chunks)
    record_chunks("direct_topic_chunks", chunks)

    if not chunks:
        return (
            "Информация по указанной теме/периоду не найдена в выбранных материалах.\n\n"
            "Попробуйте:\n"
            "• выбрать конкретный раздел;\n"
            "• выбрать другой файл;\n"
            "• переформулировать тему."
        )

    # Stage 7f: metadata header removed from user-facing output (see
    # generate_selected_section_summary). Body only.
    header = ""

    if "крат" in str(summary_type or "").lower():
        return header + build_extractive_short_summary(
            topic=topic,
            plan=[topic],
            chunk_groups=[{"item": topic, "chunks": chunks[:6]}],
        )

    compact_context = _chunks_to_context(chunks[:8])
    prompt = f"""{config.SYSTEM_PROMPT}

ТЕМА:
{topic}

НАЙДЕННЫЕ ФРАГМЕНТЫ:
{compact_context}

Задача:
Составь {summary_type.lower()} конспект только по указанной теме.

Инструкция:
1. Используй только найденные фрагменты.
2. Не включай соседние темы, даже если они находятся в той же главе.
3. Если в фрагментах есть стандарт, архитектура, уровни, протоколы, формулы или примеры — сохрани их.
4. Не подменяй тему более общей областью.
5. Не добавляй факты от себя.
6. Структурируй материал по смысловым подпунктам.
7. Для технического стандарта, если сведения есть во фрагментах, обязательно раскрой: назначение, развитие стандарта, сравнение с альтернативами, архитектуру, физический уровень, доступ к среде/MAC, QoS/сервисы, структуру кадра.
8. Не ограничивайся общими словами, сохраняй конкретные обозначения и параметры: номера стандартов, диапазоны частот, методы OFDM/OFDMA, TDD/FDD, типы модуляции, скорости, классы сервисов.
9. Если какого-то блока нет в найденных фрагментах, не выдумывай его.
10. В конце добавь короткий итог.

КОНСПЕКТ:"""

    record_prompt("direct_topic", prompt)
    result = llm.call(prompt, max_tokens=params["final_tokens"])
    return header + result

def _clean_plan_line(line):
    """Очищает строку плана от нумерации и мусора."""
    line = str(line or "").strip()
    line = re.sub(r"^\s*[\-\*\d\.\)\:]+\s*", "", line)
    line = line.strip(" -—–•\t")
    return line.strip()


def _is_bad_plan_item(item):
    """Отсекает обрезанные или слишком общие пункты плана."""
    item = str(item or "").strip()
    low = item.lower()

    if len(item) < 8:
        return True

    if low in {"план", "ответ", "поисковые подпункты"}:
        return True

    words = re.findall(r"[а-яёa-z0-9]+", low)
    if words and len(words[-1]) <= 3 and words[-1] not in {"ссср", "нэп", "рф"}:
        return True

    if re.search(r"\b(и|или|а|но|рас|пер)\s*$", low):
        return True

    return False


def _looks_like_history_topic(topic):
    """Определяет широкую историческую тему без привязки к одному учебнику."""
    low = str(topic or "").lower()
    markers = [
        "истори", "росси", "ссср", "революц", "войн", "импери",
        "николай", "ленин", "сталин", "хрущ", "брежнев",
        "горбач", "ельцин", "век", "год",
    ]
    has_year = bool(re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", low))

    return has_year or sum(1 for marker in markers if marker in low) >= 2


def _fallback_topic_plan(topic, summary_type, max_items):
    """
    Страховочный план, если LLM дала слишком короткий или битый список.
    Для истории он хронологический, для остальных дисциплин - аспектный.
    """
    low_type = str(summary_type or "").lower()
    min_items = 6 if "подроб" in low_type else 5

    if _looks_like_history_topic(topic):
        items = [
            "революция 1917 года и смена политической власти",
            "Гражданская война и формирование советской власти",
            "создание СССР, НЭП и экономическая политика 1920-х годов",
            "индустриализация, коллективизация и политическая система 1930-х годов",
            "Великая Отечественная война и советский тыл",
            "послевоенное восстановление, холодная война и внешняя политика СССР",
            "оттепель, застой и реформы советской системы",
            "перестройка, распад СССР и Россия 1990-х годов",
        ]
        limit = max(1, min(max_items, len(items)))
        if len(items) > limit:
            index_map = {
                4: [0, 1, 4, 7],
            5: [0, 1, 3, 5, 7],
                6: [0, 1, 2, 4, 5, 7],
                7: [0, 1, 2, 3, 4, 5, 7],
            }
            if limit in index_map:
                return [items[index] for index in index_map[limit]]

            indexes = [
                round(i * (len(items) - 1) / (limit - 1))
                for i in range(limit)
            ] if limit > 1 else [0]
            return [items[index] for index in indexes]
    else:
        items = [
            f"основные понятия темы: {topic}",
            f"ключевые определения и классификации: {topic}",
            f"главные свойства, принципы и механизмы: {topic}",
            f"методы, алгоритмы или этапы работы: {topic}",
            f"примеры, задачи и практическое применение: {topic}",
            f"ограничения, типичные ошибки и важные выводы: {topic}",
        ]

    return items[:max(min_items, min(max_items, len(items)))]


def build_topic_search_plan(llm, topic, summary_type, max_items=None):
    """
    Универсальный планировщик темы.

    Не пишет конспект, а создаёт поисковые подпункты.
    Работает для истории, сетей, математики, программирования и других дисциплин.
    """
    if max_items is None:
        max_items = getattr(config, "PLANNED_SUMMARY_QUERIES", 7)

    if _looks_like_history_topic(topic):
        fallback_limit = max_items - 1 if max_items > 1 else max_items
        plan = [topic] + _fallback_topic_plan(topic, summary_type, fallback_limit)
        add_event("history_plan", plan=plan)
        return plan

    prompt = f"""{config.SYSTEM_PROMPT}

ТЕМА ПОЛЬЗОВАТЕЛЯ:
{topic}

Задача:
Разбей тему на {max_items} коротких поисковых подпунктов для поиска в учебных материалах.

Важно:
1. Не пиши конспект.
2. Не добавляй факты от себя.
3. Каждый подпункт должен быть коротким поисковым запросом.
4. Подпункты должны покрывать тему с разных сторон.
5. Если тема историческая — сохраняй хронологию.
6. Если тема техническая — иди от основных понятий к деталям.
7. Если тема математическая — выдели определения, свойства, формулы, методы и примеры.

Формат:
Каждый подпункт с новой строки.
Без пояснений.

ПОИСКОВЫЕ ПОДПУНКТЫ:"""

    raw = llm.call(prompt, max_tokens=500)

    lines = []
    for line in raw.splitlines():
        item = _clean_plan_line(line)
        if not item or _is_bad_plan_item(item):
            continue
        lines.append(item)

    # Убираем дубли, сохраняя порядок
    unique = []
    seen = set()

    for item in lines:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    topic_key = topic.lower()
    unique = [item for item in unique if item.lower() != topic_key]
    fallback_limit = max_items - 1 if max_items > 1 else max_items
    fallback = _fallback_topic_plan(topic, summary_type, fallback_limit)
    min_plan_items = min(len(fallback), fallback_limit)

    # Fallback/дополнение, если модель дала слишком короткий или битый план.
    if len(unique) < min_plan_items:
        if _looks_like_history_topic(topic):
            supplemented = list(fallback)
            seen = {item.lower() for item in supplemented}
            for item in unique:
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    supplemented.append(item)
            unique = supplemented
        else:
            seen = {item.lower() for item in unique}
            for item in fallback:
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    unique.append(item)

    # Всегда держим исходную тему первым запросом, но не даём ей вытеснить
    # последний подпериод из хронологического плана.
    plan = [topic] + unique[:fallback_limit]
    add_event("llm_plan", plan=plan)
    return plan


def _dedupe_chunks(chunks):
    """Убирает дубли найденных чанков."""
    unique = []
    seen = set()

    for chunk in chunks:
        text = chunk.get("text", "").strip()
        if not text:
            continue

        key = text[:500].lower()

        if key in seen:
            continue

        seen.add(key)
        unique.append(chunk)

    return unique


def _limit_chunks_per_section(chunks, max_per_section=None):
    """
    Не даёт одному разделу забить весь конспект.
    Это важно для больших тем.
    """
    if max_per_section is None:
        max_per_section = getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS_PER_SECTION", 4)

    result = []
    section_counts = {}

    for chunk in chunks:
        section = chunk.get("section", "") or "Без раздела"
        source_file = chunk.get("source_file", "")
        key = (source_file, section)

        count = section_counts.get(key, 0)

        if count >= max_per_section:
            continue

        section_counts[key] = count + 1
        result.append(chunk)

    return result


def _select_balanced_chunks(chunks_by_query, max_chunks):
    """
    Собирает итоговую выборку так, чтобы поздние подпункты плана
    не терялись из-за ранних глав или одного очень богатого раздела.
    """
    pools = [list(chunks) for chunks in chunks_by_query if chunks]

    if not pools:
        return []

    selected = []
    seen = set()

    while pools and len(selected) < max_chunks:
        next_pools = []
        added_this_round = False

        for pool in pools:
            while pool and len(selected) < max_chunks:
                chunk = pool.pop(0)
                text = chunk.get("text", "").strip()

                if not text:
                    continue

                key = text[:500].lower()
                if key in seen:
                    continue

                seen.add(key)
                selected.append(chunk)
                added_this_round = True
                break

            if pool:
                next_pools.append(pool)

        if not added_this_round:
            break

        pools = next_pools

    return selected


def _merge_chunk_lists(*chunk_lists):
    """Склеивает несколько списков чанков без дублей, сохраняя порядок."""
    merged = []
    seen = set()

    for chunk_list in chunk_lists:
        for chunk in chunk_list or []:
            text = chunk.get("text", "").strip()
            if not text:
                continue

            key = text[:500].lower()
            if key in seen:
                continue

            seen.add(key)
            merged.append(chunk)

    return merged


def _query_variants_for_plan_item(topic, item):
    """Даёт устойчивые поисковые варианты для широких учебных тем."""
    item = str(item or "").strip()
    low = item.lower()
    variants = [item, f"{topic}. {item}"]

    if "великая отечественная" in low or "советский тыл" in low:
        variants.extend([
            "Великая Отечественная война 1941 1945 СССР",
            "советский тыл эвакуация промышленности 1941 1945",
            "СССР в годы Великой Отечественной войны",
            "ленд-лиз оборонная промышленность тыл война",
        ])

    if "оттеп" in low or "застой" in low:
        variants.extend([
            "хрущевская оттепель реформы 1953 1964",
            "Н. С. Хрущев реформы СССР",
            "брежневский застой СССР 1964 1982",
            "косыгинская реформа развитой социализм",
        ])

    if "послевоенное" in low or "холодная война" in low:
        variants.extend([
            "послевоенное восстановление СССР 1945 1953",
            "холодная война СССР внешняя политика",
            "СССР после Великой Отечественной войны",
            "советская внешняя политика холодной войны",
        ])

    if "перестрой" in low or "распад ссср" in low or "1990" in low:
        variants.extend([
            "перестройка Горбачев 1985 1991",
            "политические реформы перестройки гласность демократизация",
            "распад СССР 1991 Беловежские соглашения",
            "Россия 1990-х реформы приватизация шоковая терапия",
        ])

    if "нэп" in low or "1920" in low:
        variants.extend([
            "НЭП новая экономическая политика 1921 1928",
            "продразверстка продналог свободная торговля НЭП",
            "создание СССР 1922 союзный договор",
        ])

    unique = []
    seen = set()
    for variant in variants:
        key = variant.lower()
        if key not in seen:
            seen.add(key)
            unique.append(variant)
    return unique


def _section_keywords_for_plan_item(item):
    """Ключевые слова для быстрого добора чанков из подходящих разделов."""
    low = str(item or "").lower()

    if "великая отечественная" in low or "советский тыл" in low:
        return ["великая отечественная", "1941", "1945"]

    if "оттеп" in low or "застой" in low:
        return ["оттеп", "застой", "хрущ", "брежнев", "развитой социализм"]

    if "послевоенное" in low or "холодная война" in low:
        return ["послево", "холодная война", "1945", "1953"]

    if "перестрой" in low or "распад ссср" in low or "1990" in low:
        return ["перестрой", "распад ссср", "1990", "1991", "россия 1990"]

    if "нэп" in low or "1920" in low:
        return ["нэп", "1920", "создание ссср", "город и деревня"]

    if "гражданская" in low:
        return ["гражданская вой", "трагедия гражданской"]

    if "революц" in low or "1917" in low:
        return ["февраля к октябрю", "1917", "революционного кризиса 1917"]

    return []


def _plan_item_text_score(text, item):
    """Оценивает, насколько чанк внутри найденного раздела раскрывает пункт плана."""
    low_text = str(text or "").lower()
    low_item = str(item or "").lower()
    words = {
        word for word in re.findall(r"[а-яёa-z0-9]{4,}", low_item)
        if word not in {
            "года", "годов", "россия", "россии", "советской",
            "политика", "политики", "экономическая", "экономической",
            "формирование", "создание",
        }
    }
    score = 0

    for word in words:
        if word in low_text:
            score += 2

    for keyword in _section_keywords_for_plan_item(item):
        if keyword and keyword in low_text:
            score += 5

    if "нэп" in low_item or "1920" in low_item:
        markers = [
            "нэп", "новая экономическая политика", "продналог",
            "продразверст", "военный коммунизм", "разрух",
            "частн", "рын", "кооперац", "трест", "синдикат",
        ]
        score += sum(6 for marker in markers if marker in low_text)

    if "1990" in low_item or "распад ссср" in low_item:
        markers = [
            "1990", "1991", "1992", "ельцин", "гайдар",
            "шоков", "приватизац", "ваучер", "инфляц",
            "либерализац", "реформ", "конституц", "дефицит",
        ]
        score += sum(6 for marker in markers if marker in low_text)

    if "великая отечественная" in low_item or "советский тыл" in low_item:
        markers = ["тыл", "эвакуац", "ленд-лиз", "промышлен", "карточ"]
        score += sum(6 for marker in markers if marker in low_text)

    return score


def _needs_semantic_backup_for_history_item(item):
    """Составные исторические пункты часто не покрываются одним заголовком раздела."""
    low = str(item or "").lower()
    markers = [
        "советский тыл",
        "россия 1990",
        "1990-х",
        "экономическая политика 1920",
    ]

    return any(marker in low for marker in markers)


def _chunks_from_matching_sections(
    kb,
    item,
    file_filter="all",
    limit=6,
    *,
    workspace_id,
):
    """Быстро добирает чанки из разделов, название которых явно совпадает с пунктом."""
    if not hasattr(kb, "get_available_sections") or not hasattr(kb, "get_file_chunks"):
        return []

    keywords = _section_keywords_for_plan_item(item)
    if not keywords:
        return []

    try:
        sections = kb.get_available_sections(workspace_id=workspace_id)
    except Exception:
        return []

    matched_sections = []
    for section in sections:
        section_low = str(section or "").lower()
        matched = [keyword for keyword in keywords if keyword in section_low]
        if not matched:
            continue

        score = len(matched) * 10

        if "1990" in str(item).lower() and "россия" in section_low and "1990" in section_low:
            score += 40

        if "распад ссср" in str(item).lower() and "распад ссср" in section_low:
            score += 30

        if "перестрой" in str(item).lower() and "перестрой" in section_low:
            score += 20

        if re.search(r"глава\s+\d+", section_low):
            score += 2

        matched_sections.append((score, section))

    matched_sections.sort(key=lambda pair: pair[0], reverse=True)

    section_pools = []
    for _, section in matched_sections[:3]:
        try:
            section_chunks = kb.get_file_chunks(
                file_filter=file_filter,
                section_filter=section,
                workspace_id=workspace_id,
            )
        except Exception:
            continue

        scored_chunks = []
        for chunk in section_chunks:
            if _looks_like_noisy_chunk_text(chunk.get("text", "")):
                continue
            score = _plan_item_text_score(chunk.get("text", ""), item)
            scored_chunks.append((score, chunk))

        scored_chunks.sort(
            key=lambda pair: (
                -pair[0],
                int(pair[1].get("chunk_id", 0) or 0),
            )
        )

        selected = [chunk for score, chunk in scored_chunks if score > 0][:limit]
        if len(selected) < limit:
            selected.extend(
                chunk
                for score, chunk in scored_chunks
                if chunk not in selected
            )

        if selected:
            section_pools.append(selected[:limit])

    chunks = []
    while section_pools and len(chunks) < limit:
        next_pools = []

        for pool in section_pools:
            if not pool or len(chunks) >= limit:
                continue

            chunks.append(pool.pop(0))

            if pool:
                next_pools.append(pool)

        section_pools = next_pools

    return chunks[:limit]


def _format_group_sources(chunks, limit=3):
    """Короткая диагностика: какие разделы попали в пункт плана."""
    sections = []
    seen = set()

    for chunk in chunks:
        section = (chunk.get("section") or "Без раздела").strip()
        if _looks_like_noisy_section(section):
            continue
        if section in seen:
            continue
        seen.add(section)
        sections.append(section)
        if len(sections) >= limit:
            break

    return "; ".join(sections) if sections else "нет разделов"


def _chunks_to_context(chunks):
    """Собирает чанки в контекст для LLM."""
    context_parts = []

    for chunk in chunks:
        section = chunk.get("section", "")
        label = (
            f'{chunk["source_file"]} | {section}'
            if section
            else chunk["source_file"]
        )
        context_parts.append(f"[{label}]\n{chunk['text']}")

    return "\n\n---\n\n".join(context_parts)


def _clean_chunk_text_for_extract(text):
    """Убирает служебную метку раздела и лишние пробелы."""
    text = re.sub(r"^\s*\[[^\]]+\]\s*", "", str(text or "").strip())
    text = re.sub(r"\b([А-Яа-яЁё]{1,6})\s*[-‑]\s*([а-яё]{2,})\b", r"\1\2", text)
    split_word_replacements = {
        "вой на": "война",
        "вой ны": "войны",
        "вой не": "войне",
        "вой ска": "войска",
        "вой скам": "войскам",
        "вой ск": "войск",
        "гитлеров ские": "гитлеровские",
        "Совет ского": "Советского",
        "Совет ской": "Советской",
    }
    for broken, fixed in split_word_replacements.items():
        text = re.sub(re.escape(broken), fixed, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    return text.strip()


def _is_valid_short_sentence(sentence):
    sentence = str(sentence or "").strip()

    if len(sentence) < 60 or len(sentence) > 280:
        return False
    if sentence.startswith("["):
        return False
    if _looks_like_noisy_chunk_text(sentence):
        return False
    if re.search(r"\.{5,}\s*\d+", sentence):
        return False

    first = sentence.lstrip("«\"'(")[:1]
    if first and first.islower():
        return False
    if first in {",", ".", ";", ":", ")", "]"}:
        return False
    if re.search(r"\b[А-ЯЁ]\.?$", sentence):
        return False

    return True


def _short_summary_markers(item):
    """Ключевые маркеры для выбора хороших предложений в кратком режиме."""
    low = str(item or "").lower()

    if "гражданская" in low:
        return ["брест", "чехословац", "красн", "бел", "фронт", "советская россия"]

    if "революц" in low or "1917" in low:
        return ["февраль", "октябрь", "монарх", "временное правительство", "совнарком", "1917"]

    if "нэп" in low or "1920" in low:
        return ["нэп", "продналог", "рын", "регулирован", "город", "деревн", "разрух"]

    if "великая отечественная" in low or "советский тыл" in low:
        return ["великая отечественная", "1941", "1945", "гитлер", "герман", "барбаросса", "тыл"]

    if "послевоенное" in low or "холодная война" in low:
        return ["1945", "1950", "холодная война", "тяжелой промышленности", "ракет", "атом"]

    if "перестрой" in low or "распад ссср" in low or "1990" in low:
        return ["перестрой", "горбач", "ельцин", "1990", "1991", "приватизац", "дефицит", "суверенитет"]

    return []


def _split_extract_sentences(text):
    """Делит текст на предложения, не ломая исторические сокращения г./гг."""
    protected = str(text or "")
    replacements = {
        " гг.": " гг<prd>",
        " г.": " г<prd>",
    }
    for source, target in replacements.items():
        protected = protected.replace(source, target)

    sentences = re.split(r"(?<=[.!?])\s+", protected)
    return [
        sentence.replace("<prd>", ".")
        for sentence in sentences
    ]


def _extract_short_points(chunks, item="", max_points=2):
    """Делает короткие тезисы без LLM, чтобы краткий режим был быстрым."""
    all_candidates = []
    keywords = set(_section_keywords_for_plan_item(item))
    markers = _short_summary_markers(item)
    item_words = {
        word for word in re.findall(r"[а-яёa-z0-9]{4,}", str(item).lower())
        if word not in {"года", "годов", "системы", "политики", "советский"}
    }

    for chunk_index, chunk in enumerate(chunks):
        text = _clean_chunk_text_for_extract(chunk.get("text", ""))
        if not text:
            continue

        sentences = _split_extract_sentences(text)

        for sentence_index, sentence in enumerate(sentences):
            sentence = sentence.strip(" -–—\t")
            if not _is_valid_short_sentence(sentence):
                continue

            low = sentence.lower()
            score = 0
            score += sum(3 for keyword in keywords if keyword and keyword in low)
            score += sum(1 for word in item_words if word in low)
            score += len(re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", low))
            if re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", low):
                score += 2
            if any(marker in low for marker in ["началась", "привело", "представлял", "составлял", "создал", "заключ"]):
                score += 1
            score += sum(4 for marker in markers if marker and marker in low)

            all_candidates.append((score, chunk_index, sentence_index, sentence))

    all_candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    points = []
    has_positive_candidates = any(score > 0 for score, _, _, _ in all_candidates)

    for score, _, _, sentence in all_candidates:
        if score <= 0 and (points or has_positive_candidates):
            continue
        if sentence in points:
            continue
        points.append(sentence)

        if len(points) >= max_points:
            break

    return points


def build_extractive_short_summary(topic, plan, chunk_groups):
    """Быстрый краткий конспект без LLM."""
    lines = [
        f"**КРАТКИЙ ТЕМАТИЧЕСКИЙ КОНСПЕКТ: {topic}**",
        "",
    ]

    for group in chunk_groups:
        item = group.get("item", "")
        chunks = group.get("chunks", [])
        points = _extract_short_points(chunks, item=item, max_points=2)

        lines.append(f"### {item}")

        if not points:
            lines.append("- Нет данных в найденных фрагментах.")
        else:
            for point in points:
                lines.append(f"- {point}")

        lines.append("")

    lines.append("**Итог:** краткий режим построен быстро по найденным фрагментам; для связного пересказа используйте средний или подробный режим.")

    return "\n".join(lines)


def retrieve_chunks_by_plan(
    kb,
    topic,
    plan,
    file_filter="all",
    section_filter=None,
    target_chunks=None,
    *,
    workspace_id,
):
    """
    Ищет чанки по нескольким подпунктам плана.
    """
    configured_per_query = getattr(config, "PLANNED_SUMMARY_CHUNKS_PER_QUERY", 5)
    configured_max_chunks = getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS", 40)
    max_chunks = configured_max_chunks

    if target_chunks:
        max_chunks = int(target_chunks)

    # Для подробного конспекта одного fixed per_query мало: 7 пунктов * 5
    # фрагментов дают максимум 35 чанков, даже если параметры хотят 80+.
    plan_size = max(len(plan), 1)
    per_query = max(
        configured_per_query,
        math.ceil(max_chunks / plan_size * 1.35),
    )

    raw_chunks_by_query = []
    effective_plan = [
        item for item in plan
        if str(item or "").strip().lower() != str(topic or "").strip().lower()
    ] or [topic]

    for item in effective_plan:
        chunk_lists = []
        section_chunks = _chunks_from_matching_sections(
            kb=kb,
            item=item,
            file_filter=file_filter,
            limit=per_query,
            workspace_id=workspace_id,
        )
        if section_chunks:
            chunk_lists.append(section_chunks)

        if max_chunks > 18:
            for query in _query_variants_for_plan_item(topic, item):
                chunk_lists.append(kb.search_chunks_for_summary(
                    query=query,
                    file_filter=file_filter,
                    section_filter=section_filter,
                    top_k=per_query,
                    workspace_id=workspace_id,
                ))

        chunks = _merge_chunk_lists(*chunk_lists)
        raw_chunks_by_query.append(chunks)

    balanced_chunks = _select_balanced_chunks(raw_chunks_by_query, max_chunks * 2)

    if len(balanced_chunks) < max(8, max_chunks // 2):
        topic_backup = kb.search_chunks_for_summary(
            query=topic,
            file_filter=file_filter,
            section_filter=section_filter,
            top_k=min(per_query, max_chunks),
            workspace_id=workspace_id,
        )
        balanced_chunks = _merge_chunk_lists(balanced_chunks, topic_backup)

    if target_chunks and target_chunks >= 60:
        max_per_section = max(
            getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS_PER_SECTION", 4),
            6,
        )
    else:
        max_per_section = getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS_PER_SECTION", 4)

    balanced_chunks = _limit_chunks_per_section(
        _dedupe_chunks(balanced_chunks),
        max_per_section=max_per_section,
    )

    return balanced_chunks[:max_chunks]


def retrieve_chunk_groups_by_plan(
    kb,
    topic,
    plan,
    file_filter="all",
    section_filter=None,
    target_chunks=None,
    *,
    workspace_id,
):
    """
    Ищет чанки отдельно для каждого пункта плана.

    Это нужно для тематического конспекта: если сначала смешать все эпохи
    в одну пачку, локальная модель легче переносит факты между периодами.
    """
    configured_per_query = getattr(config, "PLANNED_SUMMARY_CHUNKS_PER_QUERY", 5)
    configured_max_chunks = getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS", 40)
    max_chunks = configured_max_chunks

    if target_chunks:
        max_chunks = int(target_chunks)

    effective_plan = [
        item for item in plan
        if str(item or "").strip().lower() != str(topic or "").strip().lower()
    ] or [topic]

    if max_chunks <= 18:
        per_query = max(1, math.ceil(max_chunks / max(len(effective_plan), 1)))
    else:
        per_query = max(
            configured_per_query,
            math.ceil(max_chunks / max(len(effective_plan), 1)),
        )

    if target_chunks and target_chunks >= 60:
        max_per_section = max(
            getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS_PER_SECTION", 4),
            6,
        )
    else:
        max_per_section = getattr(config, "PLANNED_SUMMARY_MAX_CHUNKS_PER_SECTION", 4)

    groups = []
    history_topic = _looks_like_history_topic(topic)

    for item in effective_plan:
        chunk_lists = []
        section_chunks = _chunks_from_matching_sections(
            kb=kb,
            item=item,
            file_filter=file_filter,
            limit=per_query,
            workspace_id=workspace_id,
        )
        if section_chunks:
            chunk_lists.append(section_chunks)

        should_use_semantic = (
            max_chunks > 18
            or not history_topic
        )

        if should_use_semantic:
            for query in _query_variants_for_plan_item(topic, item):
                chunk_lists.append(kb.search_chunks_for_summary(
                    query=query,
                    file_filter=file_filter,
                    section_filter=section_filter,
                    top_k=per_query,
                    workspace_id=workspace_id,
                ))

        chunks = _merge_chunk_lists(*chunk_lists)
        chunks = _limit_chunks_per_section(
            _dedupe_chunks(chunks),
            max_per_section=max_per_section,
        )

        groups.append({
            "item": item,
            "chunks": chunks[:per_query],
        })

    used_total = sum(len(group["chunks"]) for group in groups)
    if max_chunks > 18 and used_total < max(8, max_chunks // 2):
        topic_backup = kb.search_chunks_for_summary(
            query=topic,
            file_filter=file_filter,
            section_filter=section_filter,
            top_k=per_query,
            workspace_id=workspace_id,
        )
        if topic_backup:
            groups.insert(0, {
                "item": topic,
                "chunks": _dedupe_chunks(topic_backup),
            })

    return groups


def generate_planned_topic_summary(
    kb,
    llm,
    topic,
    summary_type,
    file_filter="all",
    section_filter=None,
    *,
    workspace_id,
):
    """
    Универсальный тематический конспект.

    Подходит для широких тем:
    - история России XX века;
    - компьютерные сети;
    - преобразование Лапласа;
    - ООП;
    - базы данных;
    - медицина и т.д.

    Вместо одного поиска:
    тема → план → поиск по каждому пункту → сборка конспекта.
    """
    params = _summary_generation_params(summary_type)
    set_strategy("planned_topic")

    plan = build_topic_search_plan(
        llm=llm,
        topic=topic,
        summary_type=summary_type,
        max_items=getattr(config, "PLANNED_SUMMARY_QUERIES", 7),
    )

    chunk_groups = retrieve_chunk_groups_by_plan(
        kb=kb,
        topic=topic,
        plan=plan,
        file_filter=file_filter,
        section_filter=section_filter,
        target_chunks=params["top_k"],
        workspace_id=workspace_id,
    )
    topic_chunks = [
        chunk
        for group in chunk_groups
        for chunk in group.get("chunks", [])
    ]
    add_event("planned_summary_plan", plan=plan)
    record_chunk_groups("planned_chunk_groups", chunk_groups)

    if not topic_chunks:
        return (
            "Информация по указанной теме/периоду не найдена в выбранных материалах.\n\n"
            "Попробуйте:\n"
            "• выбрать другой файл;\n"
            "• выбрать конкретный раздел;\n"
            "• переформулировать тему."
        )

    section_label = section_filter if section_filter else "Все разделы"
    plan_stats = [
        (
            f"• {group.get('item', '')}: {len(group.get('chunks', []))} "
            f"({ _format_group_sources(group.get('chunks', [])) })"
        )
        for group in chunk_groups
    ]

    # Stage 7f: metadata header removed from user-facing output (see
    # generate_selected_section_summary). Body only.
    header = ""

    if "крат" in str(summary_type or "").lower():
        return header + build_extractive_short_summary(topic, plan, chunk_groups)

    if "сред" in str(summary_type or "").lower():
        generation_groups = [
            group for group in chunk_groups
            if str(group.get("item", "")).strip().lower() != str(topic).strip().lower()
        ] or chunk_groups
        generation_plan = [group.get("item", topic) for group in generation_groups]
        compact_parts = []
        for group in generation_groups:
            item = group.get("item", topic)
            chunks = group.get("chunks", [])[:2]
            if not chunks:
                compact_parts.append(
                    f"Пункт плана: {item}\n"
                    "НЕТ ДАННЫХ В НАЙДЕННЫХ ФРАГМЕНТАХ."
                )
                continue

            compact_parts.append(
                f"Пункт плана: {item}\n"
                f"{_chunks_to_context(chunks)}"
            )

        compact_context = "\n\n---\n\n".join(compact_parts)

        medium_prompt = f"""{config.SYSTEM_PROMPT}

ТЕМА:
{topic}

ПЛАН:
{chr(10).join(f"- {item}" for item in generation_plan)}

НАЙДЕННЫЕ ФРАГМЕНТЫ ПО ПУНКТАМ:
{compact_context}

Задача:
Составь средний тематический конспект.

Инструкция:
1. Используй только найденные фрагменты.
2. Пиши строго по пунктам плана из блока "ПЛАН".
3. По каждому пункту дай 2-4 содержательных тезиса.
4. Не добавляй факты от себя.
5. Если по пункту нет полезных сведений, напиши "нет данных в найденных фрагментах".
6. Для исторических тем сохраняй хронологию.
7. Не создавай отдельный раздел с названием исходной темы "{topic}"; это только общая тема, а не пункт конспекта.
8. Если пункт плана составной, раскрывай его части отдельно внутри этого пункта, если они есть в найденных фрагментах.
9. Для пункта про перестройку, распад СССР и Россию 1990-х не ограничивайся Горбачёвым: используй найденные сведения о распаде СССР и России 1990-х, если они есть.
10. Каждый тезис должен быть прямым пересказом найденного фрагмента, а не красивым обобщением.
11. Не меняй юридический или политический статус сущностей: если во фрагменте орган назван временным, не называй его постоянным.
12. Не объединяй разные даты и процессы в одну причинно-временную цепочку, если такая связь прямо не дана во фрагментах.
13. Не используй оценочные слова вроде "демократический", "успешный", "кардинальный", если такой оценки нет во фрагментах.
14. В конце добавь короткий итог.

СРЕДНИЙ КОНСПЕКТ:"""

        record_prompt("planned_medium", medium_prompt)
        result = llm.call(
            medium_prompt,
            max_tokens=params["final_tokens"],
        )

        return header + result

    partial_summaries = []
    item_group_size = max(3, min(params["group_size"], 4))

    for group in chunk_groups:
        item = group.get("item", topic)
        chunks = group.get("chunks", [])

        if not chunks:
            partial_summaries.append(
                f"Пункт плана: {item}\n"
                "Найденные сведения: НЕТ ДАННЫХ В НАЙДЕННЫХ ФРАГМЕНТАХ."
            )
            continue

        item_partials = []

        for i in range(0, len(chunks), item_group_size):
            chunk_batch = chunks[i:i + item_group_size]
            context = _chunks_to_context(chunk_batch)

            prompt = f"""{config.SYSTEM_PROMPT}

        ТЕМА:
        {topic}

        ПУНКТ ПЛАНА:
        {item}

        ФРАГМЕНТЫ УЧЕБНОГО МАТЕРИАЛА:
        {context}

        Задача:
        Сделай промежуточный конспект только по указанному пункту плана.

        Важно:
        1. Используй только данные из фрагментов.
        2. Не добавляй факты от себя.
        3. Если часть фрагментов слабо относится к пункту плана, просто не используй её.
        4. Не смешивай этот пункт с другими периодами или темами.
        5. Если по какому-то периоду есть только 1-2 факта, напиши только эти факты и не достраивай полную картину по памяти.
        6. Не подменяй найденные сведения общеизвестным пересказом истории.
        7. Если полезных сведений нет, напиши: НЕТ ДАННЫХ В НАЙДЕННЫХ ФРАГМЕНТАХ.
        8. Пиши кратко, структурно, на русском языке.

        ПРОМЕЖУТОЧНЫЙ КОНСПЕКТ:"""

            partial = llm.call(
                prompt,
                max_tokens=params["chunk_tokens"],
            ).strip()

            if partial:
                item_partials.append(partial)

        if not item_partials:
            partial_summaries.append(
                f"Пункт плана: {item}\n"
                f"Найдено фрагментов: {len(chunks)}\n"
                "Найденные сведения: НЕТ ДАННЫХ В НАЙДЕННЫХ ФРАГМЕНТАХ."
            )
            continue

        item_context = "\n\n---\n\n".join(item_partials)

        if len(item_partials) > 1:
            item_reduce_prompt = f"""{config.SYSTEM_PROMPT}

ТЕМА:
{topic}

ПУНКТ ПЛАНА:
{item}

ЧАСТИЧНЫЕ КОНСПЕКТЫ ПО ЭТОМУ ПУНКТУ:
{item_context}

Задача:
Объедини частичные конспекты в один конспект только по этому пункту плана.

Инструкция:
1. Используй только частичные конспекты.
2. Убери повторы.
3. Не добавляй факты от себя.
4. Не смешивай этот пункт с другими периодами.
5. Если часть сведений противоречит пункту плана, не используй её.

КОНСПЕКТ ПО ПУНКТУ:"""

            item_summary = llm.call(
                item_reduce_prompt,
                max_tokens=params["chunk_tokens"],
            ).strip()
        else:
            item_summary = item_partials[0]

        if item_summary:
            partial_summaries.append(
                f"Пункт плана: {item}\n"
                f"Найдено фрагментов: {len(chunks)}\n"
                f"{item_summary}"
            )

    if not partial_summaries:
        return (
            "Информация по указанной теме не найдена в выбранных материалах.\n\n"
            "Попробуйте:\n"
            "• выбрать конкретный раздел;\n"
            "• выбрать другой файл;\n"
            "• переформулировать тему."
        )

    combined_context = "\n\n---\n\n".join(partial_summaries)

    final_prompt = f"""{config.SYSTEM_PROMPT}

ТЕМА:
{topic}

ПОИСКОВЫЙ ПЛАН:
{chr(10).join(f"- {item}" for item in plan)}

ПРОМЕЖУТОЧНЫЕ КОНСПЕКТЫ:
{combined_context}

Задача:
Составь {summary_type.lower()} итоговый тематический конспект.

Инструкция:
1. Используй только промежуточные конспекты.
2. Сохрани структуру темы.
3. Не добавляй факты от себя.
4. Строй итог строго по пунктам плана и не переноси факты из одного пункта в другой.
5. Используй только те периоды и подпункты, которые есть в плане поиска или прямо подтверждаются промежуточными конспектами.
6. Не добавляй несуществующие пункты плана и не упоминай эпохи вне заданной темы.
7. Если по пункту написано "НЕТ ДАННЫХ В НАЙДЕННЫХ ФРАГМЕНТАХ", не заполняй его по памяти.
8. Если какие-то пункты плана не раскрыты в найденных фрагментах, укажи это в конце коротким списком.
9. Для исторических тем сохраняй хронологический порядок и не выходи за верхнюю границу периода.
10. Для подробного конспекта раскрывай каждый найденный период отдельным блоком: события, процессы, последствия, участники, даты.
11. Если по периоду найдено мало данных, не дополняй его общеизвестными фактами; честно укажи только найденное.
12. Для технических тем объясняй от общего к частному.
13. Для математических тем выделяй определения, формулы, свойства и применение.
14. В конце добавь короткий итог.

ИТОГОВЫЙ КОНСПЕКТ:"""

    record_prompt("planned_reduce", final_prompt)
    result = llm.call(
        final_prompt,
        max_tokens=params["final_tokens"],
    )

    return header + result

def generate_full_file_summary(
    kb,
    llm,
    selected_file,
    selected_section,
    summary_type,
    file_filter="all",
    *,
    workspace_id,
):
    """No-topic, no-section fallback: map-reduce conspect over every chunk of
    ``file_filter`` in ``workspace_id``.

    Driven only by ``app_services.generate_summary_service`` (Stage 6d); no UI
    entrypoint is involved. The ``selected_file`` / ``selected_section``
    arguments are kept only to render the user-facing header — the actual
    retrieval is driven by ``file_filter`` + ``workspace_id``.
    """
    set_strategy("full_file_map_reduce")
    params = _summary_generation_params(summary_type)

    chunks = kb.get_file_chunks(file_filter=file_filter, workspace_id=workspace_id)
    if not chunks:
        return "НЕТ ИНФОРМАЦИИ - база пуста или файл не проиндексирован."

    # Потолок для конспекта без темы: полный map-reduce по большому материалу
    # = десятки последовательных вызовов LLM (зависание / обрыв соединения).
    # Берём первые N фрагментов; если обрезали — честно помечаем результат ниже.
    total_chunks = len(chunks)
    max_chunks = getattr(config, "FULL_FILE_SUMMARY_MAX_CHUNKS", 60)
    truncated = max_chunks > 0 and total_chunks > max_chunks
    if truncated:
        chunks = chunks[:max_chunks]

    group_size = params["group_size"]
    prompts = []

    for i in range(0, len(chunks), group_size):
        group = chunks[i:i + group_size]
        context_parts = []
        for chunk in group:
            section = chunk["section"]
            label = f'{chunk["source_file"]} | {section}' if section else chunk["source_file"]
            context_parts.append(f"[{label}]\n{chunk['text']}")
        context = "\n\n---\n\n".join(context_parts)

        prompts.append(
            config.PROMPTS["summary_chunk"].format(
                system=config.SYSTEM_PROMPT,
                context=context,
            )
        )

    # Промежуточные выжимки независимы - гоним параллельно (порядок сохраняется).
    partial_summaries = _map_call_parallel(llm, prompts, max_tokens=params["chunk_tokens"])

    combined_context = "\n\n---\n\n".join(partial_summaries)
    final_prompt = config.PROMPTS["summary_reduce"].format(
        system=config.SYSTEM_PROMPT,
        context=combined_context,
        summary_type=summary_type.lower(),
    )
    summary = llm.call(final_prompt, max_tokens=params["final_tokens"])

    file_label = "всем материалам" if selected_file == "Все файлы" else selected_file
    section_label = (
        "всем разделам"
        if selected_section == "Все разделы"
        else selected_section
    )
    # Stage 7f: metadata header removed from user-facing output (see
    # generate_selected_section_summary). The only header we keep is an honest
    # notice when a big material was capped to its first chunks above.
    header = ""
    if truncated:
        header = (
            f"_⚠️ Материал большой — конспект построен по первым {max_chunks} "
            f"фрагментам из {total_chunks}. Чтобы получить конспект по конкретному "
            f"разделу, задайте тему._\n\n"
        )

    return header + summary


def generate_topic_summary(
    kb,
    llm,
    topic,
    summary_type,
    file_filter="all",
    section_filter=None,
    *,
    workspace_id,
):
    """
    Тематический конспект через semantic search + rerank.

    Тема → HyDE → embedding → ChromaDB → rerank → лучшие чанки → map-reduce конспект.
    """
    params = _summary_generation_params(summary_type)
    set_strategy("legacy_topic")
    topic_chunks = kb.search_chunks_for_summary(
        query=topic,
        file_filter=file_filter,
        section_filter=section_filter,
        top_k=params["top_k"],
        workspace_id=workspace_id,
    )
    record_chunks("legacy_topic_chunks", topic_chunks)

    if not topic_chunks:
        return (
            "Информация по указанной теме/периоду не найдена в выбранных материалах.\n\n"
            "Попробуйте:\n"
            "• выбрать другой файл;\n"
            "• выбрать «Все разделы»;\n"
            "• указать тему иначе."
        )

    group_size = params["group_size"]
    partial_summaries = []

    for i in range(0, len(topic_chunks), group_size):
        group = topic_chunks[i:i + group_size]

        context_parts = []

        for chunk in group:
            section = chunk.get("section", "")
            label = (
                f'{chunk["source_file"]} | {section}'
                if section
                else chunk["source_file"]
            )
            context_parts.append(f"[{label}]\n{chunk['text']}")

        context = "\n\n---\n\n".join(context_parts)

        prompt = config.PROMPTS["topic_summary_chunk"].format(
            system=config.SYSTEM_PROMPT,
            topic=topic,
            context=context,
        )

        partial_raw = llm.call(prompt, max_tokens=params["chunk_tokens"])
        partial = partial_raw.strip()

        partial_clean = partial.strip()
        partial_upper = partial_clean.upper()

        if partial_clean and not partial_upper.startswith("НЕ ОТНОСИТСЯ"):
            partial_summaries.append(partial_clean)

    if not partial_summaries:
        return (
            "Информация по указанной теме/периоду не найдена в выбранных материалах.\n\n"
            "Попробуйте:\n"
            "• указать тему иначе;\n"
            "• выбрать другой файл;\n"
            "• выбрать «Все разделы»."
        )

    combined_context = "\n\n---\n\n".join(partial_summaries)

    final_prompt = config.PROMPTS["topic_summary_reduce"].format(
        system=config.SYSTEM_PROMPT,
        topic=topic,
        context=combined_context,
        summary_type=summary_type.lower(),
    )

    result = llm.call(final_prompt, max_tokens=params["final_tokens"])

    section_label = section_filter if section_filter else "Все разделы"

    # Stage 7f: metadata header removed from user-facing output (see
    # generate_selected_section_summary). Body only.
    header = ""

    return header + result
