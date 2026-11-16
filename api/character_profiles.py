"""Volume dossiers stored as readable text in the existing cast records."""

from fastapi import HTTPException


def validate_profiles(items, context, target_volume_id=None):
    volumes = {row["id"]: row for row in context["volumes"]}
    for item in items:
        profiles = item.volume_profiles
        ids = [profile.volume_id for profile in profiles]
        if not profiles or len(set(ids)) != len(ids) or (target_volume_id is not None and ids != [target_volume_id]):
            raise HTTPException(502, "模型未返回完整的目标分卷角色档案，本批次未保存")
        for profile in profiles:
            volume = volumes.get(profile.volume_id)
            if volume is None:
                raise HTTPException(502, "角色阶段档案引用了其他大纲的分卷，本批次未保存")
            beats = {row["id"] for row in volume["beats"]}
            if (len(set(profile.plot_beat_ids)) != len(profile.plot_beat_ids)
                    or not set(profile.plot_beat_ids) <= beats or (beats and not profile.plot_beat_ids)):
                raise HTTPException(502, "角色阶段档案引用的剧情节点无效，本批次未保存")


def format_profile(profile, sources):
    volume = next(row for name, row in sources if name == "novel_outline_volume" and row["id"] == profile.volume_id)
    outline = next(row for name, row in sources if name == "novel_outline" and row["id"] == volume["outline_id"])
    beats = {row["id"]: row for name, row in sources if name == "novel_plot_beat" and row["volume_id"] == profile.volume_id}
    basis = "、".join(beats[beat_id]["title"] for beat_id in profile.plot_beat_ids) or "分卷摘要与冲突"
    if len(basis) > 500:
        basis = basis[:500] + "…"
    # All generated outcomes are plans. A written chapter count cannot establish
    # that a particular character action or ending has already happened.
    return (f"【大纲计划 · v{outline['version_no']} · 第{volume['volume_number']}卷】\n"
            f"【剧情依据】\n{basis}\n"
            f"【本部分行动（计划）】\n{profile.actions}\n"
            f"【阶段结果（计划）】\n{profile.outcome}\n"
            f"【与小说进度的关系（生成时）】\n{profile.progress_relation}\n"
            "【说明】\n以上为大纲安排，不代表正文已发生；实际进展以已保存正文为准。")
