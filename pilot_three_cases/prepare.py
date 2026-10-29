"""Create versioned pilot configuration from the supplied plan; never handles keys."""
import argparse
import re
import shutil
from pathlib import Path
from generator import ROOT, write_json, now


def card(number, events, experiences, details):
    return {"session_number": number, "relative_week": number, "life_events": events,
            "private_experiences": experiences, "still_unspoken_details": details}


def plans():
    return [
        {
            "case_id": "C01", "synthetic": True,
            "client_profile": {
                "name": "林岚", "age": 28, "gender": "女", "occupation": "品牌设计师",
                "living_context": "在杭州一家中型设计公司工作三年，独自租房，母亲住在同城。",
                "relationships": "项目负责人周姐比自己年长；同组小许是合作一年多的同事；母亲与自己联系较多。",
                "speech_style": "谨慎细致，会描述具体改动和时间；拿不准时会说‘可能吧’或自我修正，较少主动谈大道理。",
            },
            "private_life_history": [
                "工作交付通常按时，但自己常在正式交付前多次检查。",
                "半年前一次客户提案中，负责人当众指出一个尺寸错误；当时脸很热，后来几天反复想起。",
                "母亲关心自己，但有时会把亲戚孩子的工作成绩拿来聊天。",
                "开始这段咨询前没有接受过心理治疗；希望谈工作结束后仍放不下方案的困扰。",
            ],
            "session_cards": [
                card(1,
                     ["周二内部提案会上，小许说首页‘有点挤’，没说方案不能用。", "林岚当天回去修改到晚上十一点半，改了六版；第二天又把其中两处改回原样。", "没有收到客户正式否定，原定交付仍是周五。"],
                     ["听见‘挤’时先觉得脸热，猜小许可能觉得自己没想清楚。", "不知道修改是否必要；停下时总怕别人看出自己不够专业。", "本来想只谈怎么少加班，也觉得反复修改让自己很累。"],
                     ["小许平时说话直接，其他同事也会被他提意见。", "周姐说过方案整体可继续推进，没有要求推倒重做。", "六版里最后两版只是间距和标题字号变化。", "当晚取消了原本想去的瑜伽课；没有向同事说明自己修改到多晚。"]),
                card(2,
                     ["另一张活动海报准备交付前，先后找小许和周姐检查，又把图片发给一位设计朋友。", "三个人都没有发现明显错误，林岚仍在发送前检查了四遍。"],
                     ["别人说没问题时能轻松一会儿，过后又想到他们可能没细看。", "最怕漏掉一个很基础的地方，让别人觉得自己做事不行。"],
                     ["有一处颜色在电脑和手机上稍有不同，并不影响规定的印刷版本。", "朋友回复得很简短，自己反倒更想再找人看。"]),
                card(3,
                     ["母亲预约体检，需要林岚帮忙安排交通；结果是常规复查，没有急病。", "同周赶两个项目，晚上陪母亲后又打开电脑检查设计文件。"],
                     ["想把母亲和工作都照顾好，觉得自己没有做好哪一边。", "听到亲戚升职的消息有点烦，又不想显得自己计较。"],
                     ["母亲并没要求她当晚赶工作。", "打开文件后主要检查已经确认过的对齐细节，未发生实际交付事故。"]),
                card(4,
                     ["一次组内非正式小图讨论里，小许建议换颜色，林岚说明了保留原色的理由。", "小许接受先保留原色，最后讨论顺利结束。"],
                     ["讲完后有点意外，也有一点高兴。", "这张图不直接给客户，心里觉得即使说得一般也没那么严重。"],
                     ["开口前想过解释两句就停。", "下班仍看了一次聊天记录，确认没人继续质疑。"]),
                card(5,
                     ["重要客户通过周姐提出希望标题更突出，没否定整体方案。", "林岚在两个晚上分别改了七版和四版，反复预演客户会怎么看。"],
                     ["客户的意见比熟悉同事的意见更难放下。", "想到上次顺利讨论，但这次一停手就担心交出去显得自己没水平。"],
                     ["周姐明确说先改标题，其他版式可以不动。", "没出现明确投诉或绩效处罚。"]),
                card(6,
                     ["同周小许和周姐分别提出修改建议；小许的建议较容易讨论，周姐说完后林岚晚上一直回想。"],
                     ["觉得周姐会影响项目分配，自己更希望得到她肯定。", "对朋友的建议能有时不采纳，但也不是每次都轻松。"],
                     ["周姐曾肯定她做事可靠。", "回想时会把语气冷淡也理解成对自己的不满意，但没有核实。"]),
                card(7,
                     ["与熟悉同事讨论时有两次直接说明理由，没有先找人检查。", "准备向客户展示关键页面前，依然把文件反复打开修改。"],
                     ["有些场景比以前敢说话，但重要展示前还是很难停下来。", "怕一说自己‘好一点’就得证明以后都能做到。"],
                     ["客户展示前的修改导致又一次错过晚饭时间。", "普通讨论顺利并未使关键展示时的担心消失。"]),
                card(8,
                     ["新项目收到周姐两条修改建议，林岚晚上改到很晚，次日又打开文件。", "这周一次普通讨论里也坚持过自己的版式理由。"],
                     ["重要反馈后还是担心自己不够专业，修改当时能暂时安心。", "这次有一项修改确实改善了页面，另一些只是自己拿不准。"],
                     ["尚未告诉咨询师反复打开文件时脑子里的话。", "普通讨论后没有一直检查记录，这次重要反馈后却一直打开文件。"]),
            ],
        },
        {
            "case_id": "C02", "synthetic": True,
            "client_profile": {
                "name": "陈宁", "age": 25, "gender": "女", "occupation": "电商运营专员",
                "living_context": "在南京一家电商公司工作两年，与大学朋友合租，工作日通勤约四十分钟。",
                "relationships": "同组同事阿敏常临时请她帮忙；资深同事赵哥入职时带过她；室友悦悦知道她常加班。",
                "speech_style": "会先替别人解释，有时把不满说成‘也不是多大的事’；描述事情时会提到具体时间和个人安排，口语自然。",
            },
            "private_life_history": [
                "刚入职时赵哥耐心教过自己，至今记得这份帮助。",
                "平时收到别人请求常很快答应，过后再挤自己的时间。",
                "喜欢周末做饭和看电影，但最近个人安排多次被加班打断。",
                "开始这段咨询前没有咨询经历；主要想谈最近很累、又不好意思对同事说的情况。",
            ],
            "session_cards": [
                card(1,
                     ["周四下午四点半，阿敏请陈宁帮忙整理活动数据，原说‘顺手弄一下’。", "陈宁当天还有自己的报表要完成，仍答应；整理数据到晚上九点多。", "原本约好与室友一起吃晚饭，后来发消息取消。"],
                     ["听到请求时其实不想接，嘴上先说了‘行’。", "怕直接拒绝显得不好相处，也觉得阿敏可能确实忙。", "看到室友说饭留在冰箱有点委屈，又觉得自己没资格怪别人。"],
                     ["阿敏不是上级，没有要求陈宁必须负责这项工作。", "阿敏后来道了谢，没问她是不是有自己的任务。", "没有明确问过阿敏能否晚一点交。", "回家后还花了约四十分钟补自己的报表，没有被领导批评。"]),
                card(2,
                     ["同事请她周末帮看一份推广排期，陈宁输入过拒绝的话，最后删掉并答应。", "周六花了两个小时处理，本来安排买菜做饭的时间被打乱。"],
                     ["想到对方可能不高兴，心里就很不踏实。", "答应后短时间松口气，周六实际做时又烦。"],
                     ["消息里对方用了‘如果你方便’，她没有问不方便会怎样。", "没有明确证据表明同事拒绝后会疏远她。"]),
                card(3,
                     ["一周两次晚睡处理额外工作，早上起床很疲惫。", "室友提出一起去看电影，陈宁因为补工作再次取消。"],
                     ["对被打断的生活有些生气，又觉得大家都忙，自己也该帮。", "睡前会想明天别人会不会又来找自己。"],
                     ["没有严重持续失眠或危机事件，只是这一周入睡推迟。", "主管并不知道她替同事接了多少工作。"]),
                card(4,
                     ["阿敏再次请她帮忙，陈宁说明手上的任务，问是否可以第二天下午给。", "阿敏接受这个时间，陈宁按商量的时间完成。"],
                     ["说之前心跳快，发送后不断看消息。", "对方很快答应，有点松口气，又觉得这次可能只是对方心情好。"],
                     ["没有直接拒绝任务，而是协商了交付时间。", "当晚终于按时与室友吃了饭。"]),
                card(5,
                     ["本周两次说明自己的工作安排，其中一次同事改为找其他人。", "陈宁按时完成自己的排期，下班后仍想要不要再发消息问是否需要帮忙。"],
                     ["知道自己的任务需要时间，表达时比之前稳一些。", "别人找了其他人后还是有内疚，怕自己显得冷淡。"],
                     ["同事之后仍正常聊天，没有出现排斥。", "没有把内疚完全消除，也没有所有请求都拒绝。"]),
                card(6,
                     ["赵哥请她临时处理一份渠道汇总，陈宁本想说明已经排满，最终答应当天完成。", "当天又加班，但对普通同事的请求仍会询问时间。"],
                     ["一想到赵哥曾帮过自己，就觉得说不出口。", "对这次答应有点懊恼，同时觉得和其他人的请求不太一样。"],
                     ["赵哥没有拿过去的帮助要求回报。", "前两天阿敏请她帮忙时，她仍先问了对方的截止时间。"]),
                card(7,
                     ["同组再有人临时求助，陈宁先问截止时间和具体工作量，再决定自己能做哪部分。", "她保留了周末与室友的原定安排。"],
                     ["开始觉得自己的时间也需要提前考虑。", "帮得上仍愿意帮，但不再每次立刻说行；有时还是担心别人失望。"],
                     ["对赵哥的请求仍更难处理。", "有一次发出回复后还是回看了两遍，怕语气显得生硬。"]),
                card(8,
                     ["阿敏周五临时请她整理一份数据，陈宁这次没有当晚全部接下来，只答应看一个关键数字。", "其他部分由阿敏第二天处理，双方没有争吵。"],
                     ["先盘算了自己的报表和晚上安排，再说能帮哪一部分。", "回复后有一点内疚，但没有不断补发消息把任务接回。"],
                     ["起初只告诉咨询师自己这次没有全接，不一次说明全部理由。", "她仍重视同事关系，同时也想把原定事情做完。"]),
            ],
        },
        {
            "case_id": "C03", "synthetic": True,
            "client_profile": {
                "name": "许哲", "age": 27, "gender": "男", "occupation": "后端软件工程师",
                "living_context": "在深圳一家软件公司工作两年多，独自租房，参与业务接口和内部服务开发。",
                "relationships": "同组工程师老韩愿意讨论技术问题；技术负责人陆工工作忙、沟通较简洁；与同学偶尔线上聊天。",
                "speech_style": "简洁具体，先描述发生了什么，偶尔用排查、接口、日志等日常工作词；感受常先说‘有点烦’，追问后会补充。",
            },
            "private_life_history": [
                "大学和入职早期习惯自己查资料解决问题，独立解决后会有成就感。",
                "入职半年时问过一个配置问题，有同事随口说‘这个都没弄过？’，那句话一直记着；那位同事已离职。",
                "老韩平时交流友好，但自己以前较少直接向他提技术困难。",
                "开始这段咨询前没有咨询经历；想谈最近遇到问题总拖到很累、又不想让同事知道的情况。",
            ],
            "session_cards": [
                card(1,
                     ["本周一个接口间歇报错，许哲自己排查了两天，查日志和资料都没定位清楚。", "老韩在群里问过进展，许哲说‘还在看’，没有主动找他一起查。", "问题尚未解决，但还没到对客户交付的截止时间。"],
                     ["想到开口要先解释自己卡住了两天，觉得有点难堪。", "怕别人觉得这么久都查不出来是自己水平不够；也告诉自己再查一点就好。", "来咨询时想说如何不一直被这个问题占着，未打算一开始就讲怕别人怎么看。"],
                     ["老韩之前有一次主动帮别人定位问题，没有责备。", "两天里已试过重新查看配置和查调用链日志，没有实质进展。", "越到第二天越难开口，担心被问之前两天在干什么。", "晚上回家也继续查资料，第二天很疲惫。"]),
                card(2,
                     ["后来老韩问到细节，一起看了二十分钟，发现一个调用超时配置问题。", "老韩只说这类问题不容易从单条日志看出，没有指责许哲。"],
                     ["问题解决时松了口气，但仍觉得自己早该想到。", "害怕被当成每次都要别人帮的人，所以对下一次开口还是拿不准。"],
                     ["这次帮助没有影响陆工分配任务。", "想起那位已离职同事过去说过的话。"]),
                card(3,
                     ["与老韩一起做一个服务联调，工作中几次主动交换信息。", "有一次没弄懂协议字段，犹豫后在合作讨论中问了。"],
                     ["一起做任务时询问比单独跑去承认卡住更容易。", "老韩自然解释后，自己有点意外，仍不确定换个人是否一样。"],
                     ["其中一处问题也是老韩不知道，两人一起查了文档。", "仍会先想清楚自己试过什么再开口。"]),
                card(4,
                     ["接触一个没用过的消息组件，许哲先看文档，再主动请老韩介绍一次实际配置。", "老韩解释后两人完成测试，交流正常。"],
                     ["这次觉得新东西不熟悉可以说清楚，没有拖到很晚。", "开口前仍有一点紧张，但没一直想对方会怎么看。"],
                     ["他说明自己查到哪里，而不是直接要别人替自己做。", "老韩也提到第一次接触时踩过类似的坑。"]),
                card(5,
                     ["另一项跨服务问题排查半天后，许哲主动找陆工确认一种实现方式。", "陆工回复简短但具体，工作顺利推进；后来他也问过另一位同事一个问题。"],
                     ["面对不太熟的人还是先斟酌说法，但可以把问题说出来。", "开始觉得请教不一定意味着自己不会做整件事。"],
                     ["陆工没有额外批评，也没因此减少他的任务。", "并非每遇到问题就立即找人。"]),
                card(6,
                     ["熟悉模块出现一个缓存失效问题，许哲列出两种可能并自己检查，两小时内解决。", "没有找同事求助，按时完成当天工作。"],
                     ["这次手里有具体线索，自己查的时候比较有把握。", "解决后觉得挺顺，也没想着把过程藏起来。"],
                     ["在组会里自然讲了排查经过。", "如果两种检查都不奏效，原本打算再找老韩讨论。"]),
                card(7,
                     ["本周一个熟悉问题自己处理，另一个陌生组件问题找同事讨论。", "两项任务均按时完成。"],
                     ["现在更常先看手里有没有线索、自己查多久合适。", "偶尔仍怕问得太基础，但已不觉得每次求助都丢人。"],
                     ["希望能自己做的部分先做，需要讨论时也不一直拖。", "对这一做法还在尝试，没有绝对固定的时间规则。"]),
                card(8,
                     ["又遇到一个问题，许哲目前先自己看，还没有找同事。", "问题位于熟悉模块，已发现一个可验证的配置差异，计划当天上午尝试；截止时间在两天后。"],
                     ["这次主要是觉得有线索、想先验证，不是担心别人知道自己卡住。", "如果到中午仍没进展，会联系老韩；想到开口没有明显难堪。"],
                     ["最近又遇到问题、尚未找同事这件事是本次想谈的事情。", "尚未向咨询师说明线索、时间安排和请教的打算。"]),
            ],
        },
    ]


RESEARCH = {
    "C01": {"trajectory": "稳定模式中的情境波动", "focus": "避免把一次低风险积极行为推广成整体改变", "evidence_sessions": [2, 4, 5, 7, 8]},
    "C02": {"trajectory": "逐渐变化并有特定关系中的反复", "focus": "结合关系、内疚和实际选择理解变化，不把S6等同全面倒退", "evidence_sessions": [2, 4, 5, 6, 7, 8]},
    "C03": {"trajectory": "相似表面行为的动机变化", "focus": "S8不求助的原因须探索，不能直接套入早期能力评价担忧", "evidence_sessions": [2, 4, 5, 6, 7, 8]},
}


def prepare(source):
    if (ROOT / "configs" / "generation_config.json").exists():
        raise RuntimeError("Configuration already exists. Do not overwrite a versioned experiment.")
    text = source.read_text(encoding="utf-8-sig")
    client = re.search(r"## Agent A：Client Simulator\s*```\s*([\s\S]*?)```", text).group(1).strip()
    counselor = re.search(r"## Agent B：Counselor Simulator\s*```\s*([\s\S]*?)```", text).group(1).strip()
    for directory in ("configs/case_plans", "configs/research_plans", "dialogues", "checkpoints", "audit/human_review_forms", "generation_logs", "facts", "docs"):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    (ROOT / "configs/client_system_prompt.txt").write_text(client + "\n", encoding="utf-8")
    (ROOT / "configs/counselor_system_prompt.txt").write_text(counselor + "\n", encoding="utf-8")
    shutil.copyfile(str(source), str(ROOT / "docs/source_generation_plan.md"))
    write_json(ROOT / "configs/generation_config.json", {
        "schema_version": "1.0", "model": "qwen3.8-27b",
        "base_url": "https://ws-i2op5gcevai25bra.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "api_format": "responses", "api_key_env": "DASHSCOPE_API_KEY", "prompt_version": "pilot-v1-original-prompts",
        "temperature": 0.7, "reasoning_effort": "low", "max_output_tokens": 4096,
        "stream": False, "store": False, "min_pairs": 8, "target_pairs": 10, "max_pairs": 12,
        "request_timeout_seconds": 120, "max_retries": 2, "human_gate": "all_three_S1_before_S2", "created_at": now(),
    })
    scores = {"synthetic": True, "threshold": 4, "averaging": "mean_of_six_human_scores", "sessions": []}
    for plan in plans():
        case_id = plan["case_id"]
        write_json(ROOT / "configs/case_plans" / (case_id + ".json"), plan)
        write_json(ROOT / "configs/research_plans" / (case_id + ".json"), {
            "case_id": case_id, "visibility": "researcher_only", **RESEARCH[case_id],
            "checkpoint_candidates": {"CP1": [2, 3], "CP2": [5, 6], "CP3": [8]},
            "note": "生成计划不是对话证据或Gold；最终标注以可见对话为依据。",
        })
        (ROOT / "dialogues" / case_id).mkdir(exist_ok=True)
        write_json(ROOT / "checkpoints" / (case_id + ".json"), {
            "case_id": case_id, "status": "deferred_until_full_case", "checkpoints": [],
            "note": "本轮仅S1，尚未冻结评测样本。",
        })
        write_json(ROOT / "facts" / (case_id + ".json"), {
            "case_id": case_id, "stable_profile": plan["client_profile"], "confirmed_additions": [],
            "pending_dialogue_reviews": [], "rejected_candidates": [],
            "note": "事实确认须附对话证据并检查与人物档案和已知事实的一致性；私人体验不等同Gold。",
        })
        reviewers = [
            {"kind": "human", "slot": slot, "reviewer_id": None,
             "scores": {"naturalness": None, "counseling_quality": None, "longitudinal_plausibility": None},
             "major_issues": None, "notes": "", "reviewed_at": None}
            for slot in (1, 2)
        ]
        scores["sessions"].append({"case_id": case_id, "session_number": 1, "status": "pending", "reviewers": reviewers,
                                   "mean_score": None, "note": "S1的纵向合理性仅评起点与可延展性，完整心理轨迹留待S2–S8评价。"})
        form = """# {case_id} · S1 人工审阅表

> 拟真合成咨询材料。由两位独立人工审阅者分别填写；模型预审不计入人工得分。

阅读材料：`dialogues/{case_id}/S01.md`。人物事实可对照 `configs/case_plans/{case_id}.json`。

| 评价项 | 审阅者1 | 审阅者2 |
| --- | --- | --- |
| 审阅者身份/编号 | 待填写 | 待填写 |
| 自然性（1–5） | 待填写 | 待填写 |
| 咨询质量（1–5） | 待填写 | 待填写 |
| 纵向合理性：S1起点可信度与可延展性（1–5） | 待填写 | 待填写 |
| 重大事实冲突、Gold泄漏或严重不当咨询行为（无/列明） | 待填写 | 待填写 |
| 需要修订的发言编号及理由 | 待填写 | 待填写 |
| 审阅日期 | 待填写 | 待填写 |

评分参考：1=不可用，2=问题明显，3=需修订，4=可用且可信，5=自然、具体、探索充分。

重点核查：来访者是否逐步透露而非朗读完整解释；咨询师是否实际参与理解的形成；细节是否前后一致；当前咨询是否保留了合理的不确定性。

通过条件：两位不同审阅者完成全部六个评分，平均≥4，且均确认无重大问题。S1不代表完整纵向轨迹已通过审阅。

人工反馈以本表或逐条意见提供，再记录到 `audit/quality_scores.json`；没有反馈时保持待审阅。
""".format(case_id=case_id)
        (ROOT / "audit/human_review_forms" / (case_id + "_S01.md")).write_text(form, encoding="utf-8")
    write_json(ROOT / "audit/quality_scores.json", scores)
    write_json(ROOT / "audit/continuity_issues.json", {"status": "not_reviewed", "sessions": []})
    write_json(ROOT / "audit/model_pre_review.json", {"kind": "model_pre_review", "sessions": []})
    (ROOT / "audit/review_notes.md").write_text("# S1 审阅记录\n\n生成完成后补充模型预审，人工评分保持待审阅。\n", encoding="utf-8")
    write_json(ROOT / "generation_logs/generation_metadata.json", {"runs": []})
    print("Prepared 3 profiles, 24 cards, isolated research plans and blank human review forms.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, required=True)
    prepare(parser.parse_args().source_plan)
