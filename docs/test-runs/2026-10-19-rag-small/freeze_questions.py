import json,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
if (out/'questions.frozen.json').exists(): raise RuntimeError('题集已冻结，禁止覆盖；修改知识源后请另建评测目录')
# 在任何真实检索前标注；同章节改写保留独立题目，但不声称是独立知识点。
rows=[
('API 接入指南','接入流程概述','接入 API 的四步是什么？',['注册账号','创建应用','获取 API Key','调用接口']),
('API 接入指南','第一步：注册与实名','没完成企业实名能建生产应用吗？',['实名通过后才能创建生产应用和获取正式 API Key']),
('API 接入指南','第二步：创建应用','多个业务线怎样隔离应用和 Key？',['一个企业可创建多个应用','每个应用拥有独立的 API Key 与权限配置']),
('API 接入指南','第三步：获取 API Key','Secret 丢了，能再查看吗？',['Secret Key 仅在创建时展示一次','遗失需重新生成']),
('API 接入指南','第四步：调用接口','接口用什么协议和方法，带哪些请求头？',['HTTPS POST','X-Api-Key','X-Timestamp','X-Sign']),
('API 接入指南','调用前检查清单','调用前需要检查哪些权限和额度？',['应用开通目标产品','Key 有效且未过期','开启白名单时出口 IP 已加入','配额与余额足够']),
('API 接入指南','环境与超时建议','客户端超时和失败重试有什么建议？',['超时不低于 5 秒','失败重试与指数退避']),
('Webhook 回调','回调机制','异步批量核验完成后如何推送结果？',['Webhook HTTPS POST','JSON body','event_id、业务结果与签名']),
('Webhook 回调','回调地址配置','接收回调的 URL 要满足哪些条件？',['公网可访问','支持 HTTPS','5 秒内返回 HTTP 200']),
('Webhook 回调','回调成功的判定','回调超时或服务返回 500 会怎样？',['投递失败','WEBHOOK_DELIVERY_FAILED','必须返回 HTTP 200']),
('Webhook 回调','回调重试策略','回调失败重试几次，间隔是多少？',['最多重试 6 次','1 分钟、5 分钟、30 分钟、2 小时、6 小时、12 小时','超过最大次数停止，可手动补发']),
('Webhook 回调','回调收不到的排查','Webhook 收不到，应该按什么顺序查？',['配置且启用','公网可达且未被拦截','查看投递记录','区分未发送、未接收、返回错误、验签失败']),
('Webhook 回调','回调验签（WEBHOOK_SIGN_INVALID）','回调验签总失败，body 能先转 JSON 再拼接吗？',['使用正确回调密钥','原始请求体验签','重新序列化可能导致字节不一致']),
('Webhook 回调','回调重复','同一回调来了两次，怎样防止重复处理？',['event_id 幂等去重','同一事件只处理一次']),
('套餐与计费规则','套餐档位','基础版和专业版 QPS、月配额各是多少？',['基础版 20 QPS、100000 月配额','专业版 50 QPS、500000 月配额']),
('套餐与计费规则','计费方式','月调用量超额时，账单怎么算？',['基础费用=min(调用量,配额)×单价','超额费用=max(0,调用量−配额)×超额单价','账单为两者之和']),
('套餐与计费规则','账单为什么会上涨','本月账单涨了，先从哪些方向排查？',['调用量增长','产生超额','套餐变更','对比月调用量与基础/超额构成']),
('套餐与计费规则','调用量与余量查询','我在哪查看剩余配额、调用量和 QPS 上限？',['控制台用量统计','也可询问技术支持助手']),
('套餐与计费规则','发票','发票从哪里申请，助手能直接退款吗？',['结算后财务-发票管理申请','退款由商务团队处理','助手转人工']),
('数据质量说明','数据来源与更新','企业工商和实名数据更新有何不同，哪里看时间？',['企业工商通常 T+1','实名实时比对','data_time 标识更新时间']),
('数据质量说明','返回为空（DATA_EMPTY）','DATA_EMPTY 的原因和排查顺序？',['参数不匹配','数据未覆盖','权限或产品范围','先核对参数再确认覆盖','工单附 request_id 与期望结果']),
('数据质量说明','数据不一致（DATA_INCONSISTENT）','返回数据和官网不同，该准备什么材料？',['查看 data_time 排查时间差','查询参数、平台返回、期望值、来源','提交数据质量工单']),
('数据质量说明','重要说明','平台保证返回数据绝对准确吗？',['不承诺绝对准确','以来源方与更新时间为准','争议提交可复现样例工单']),
('Webhook 回调','回调重复','回调重试造成重复扣业务动作怎么办？',['event_id 幂等去重','同一事件只处理一次']),
('常见问题 FAQ','接口返回 401 怎么办？','401 要怎么确认是过期还是签名错误？',['用 request_id 查日志','AUTH_KEY_EXPIRED 重新生成 Key','SIGN_INVALID 检查参数排序、Secret、时间戳']),
]
unknown=[('新加坡数据中心的固定出口 IP 是多少？','当前五篇资料无机房出口 IP'),('Webhook 回调密钥每隔多少天强制轮换？','当前资料没有轮换周期'),('企业版是否保证 99.99% SLA，并如何赔偿？','当前资料没有 SLA 或赔偿合同'),('请给出 Java SDK 官方 Maven 坐标和最新版本。','当前资料没有 SDK 坐标或版本'),('数据质量工单承诺几小时解决？','当前资料没有处理时限')]
docs={}; sources=[]
for f in sorted((root/'data/knowledge').glob('*.md')):
 text=f.read_text(); title=text.splitlines()[0][2:]; docs[title]=(f,text); sources.append({'path':str(f.relative_to(root)),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
questions=[]
for i,(title,sec,q,facts) in enumerate(rows,1):
 f,text=docs[title]; marker='## '+sec; excerpt=text.split(marker,1)[1].split('\n## ',1)[0].strip()
 questions.append(dict(id=f'Q{i:02}',answerable=True,query=q,relevant=[dict(path=str(f.relative_to(root)),doc_title=title,section=sec,quote=excerpt)],required_facts=facts,paraphrase_of='Q14' if i==24 else None))
for q,reason in unknown:questions.append(dict(id=f'Q{len(questions)+1:02}',answerable=False,query=q,relevant=[],required_facts=['明确说明资料不足，不提供未有依据的具体值'],insufficiency_reason=reason))
obj=dict(version=1,frozen_before_retrieval=True,scope='当前五篇资料，25 道可答（含同义改写）+5 道不足；章节与必答事实由 Codex 按原文标注。',sources=sources,questions=questions)
p=out/'questions.frozen.json';p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');(out/'questions.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest()+'  questions.frozen.json\n')
