# P2-T12 人工審閱表：假設與事實區分（S07／S08 1.0.0）

計畫門檻：人工審閱「假設與事實區分正確率」≥ 0.90。以下由錄製回應（gpt-5-mini，1.0.0）整理，每一列是一個需要判定的項目。
請在「判定」欄填 ✓（區分正確）或 ✗（錯誤，附一句原因）。正確率 = ✓ 數 ÷ 列數。

判定準則：S07 的 confirmed 只能來自 `ncu-` 官方來源的肯定說明，其他（社群說法、「疑似」「初步研判」、模型推論）都應為 hypothesized，描述不得寫成定論；S08 的措施對待查證原因不得寫成已確定（應為查證，或「若查證屬實，…」）。

## S07 root-cause-analyst

| # | case | 來源 | basis | 原因描述 | 原句 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | normal-001 | ncu-oga-news | confirmed | 變電箱老舊跳脫導致停電。 | 停電原因為變電箱老舊跳脫 | ✓ |
| 2 | normal-002 | ncu-epsc | confirmed | 實驗廢液混合不當產生刺激性氣體。 | 經查為實驗廢液混合不當產生刺激性氣體 | ✓ |
| 3 | normal-003 | facebook | hypothesized | 疑似鍋爐故障導致熱水中斷。 | 聽說是鍋爐壞掉一直沒修好 | ✓ |
| 4 | normal-003 | facebook | hypothesized | 疑似維修延誤或保養不足，導致本週多次熱水中斷。 | 這禮拜第三次 | ✓ |
| 5 | normal-004 | facebook | hypothesized | 疑似便當內的滷蛋未熟導致多人腸胃不適。 | 大家都說是滷蛋沒熟。 | ✓ |
| 6 | normal-005 | ncu-oga-news | confirmed | 自來水公司進行外部管線搶修導致停水。 | 原因為自來水公司進行外部管線搶修 | ✓ |
| 7 | normal-005 | facebook | hypothesized | 疑似學校清洗水塔忘記公告導致停水。 | 我覺得根本是學校在清洗水塔忘記公告吧。 | ✓ |
| 8 | normal-006 | ncu-cc | confirmed | 機房核心交換器故障造成校園網路中斷。 | 今日上午九點至十點校園網路中斷，係因機房核心交換器故障，已更換備品恢復服務。 | ✓ |
| 9 | normal-007 | ncu-oga-news | confirmed | 昨晚大雨造成步道積水。 | 昨晚大雨造成中大湖周邊步道積水 | ✓ |
| 10 | normal-007 | ncu-oga-news | hypothesized | 疑似排水溝堵塞導致積水。 | 初步研判可能是排水溝堵塞 | ✓ |
| 11 | normal-008 | facebook | hypothesized | 疑似監視器故障導致監控不足，讓小偷有機可乘。 | 管理員說那邊的監視器已經壞好幾個月了 | ✓ |
| 12 | normal-009 | ncu-osa-life | confirmed | 經衛生局檢驗確認為諾羅病毒感染。 | 經衛生局檢驗，近日宿舍學生腸胃炎群聚確認為諾羅病毒感染，請同學勤洗手。 | ✓ |
| 13 | normal-009 | facebook | hypothesized | 疑似宿舍飲水機濾心太久沒更換導致污染並成為感染來源。 | 一定是宿舍飲水機太久沒換濾心，大家喝了才出事。 | ✓ |
| 14 | normal-010 | facebook | hypothesized | 疑似有人私自使用電暖器導致跳電。 | 一定是有人偷用電暖器 | ✓ |
| 15 | normal-011 | ncu-aa | confirmed | 因颱風來襲，桃園市政府宣布停止上班上課，故本校全日停課。 | 因颱風來襲，桃園市政府宣布明日停止上班上課，本校全日停課。 | ✓ |
| 16 | normal-012 | ncu-lib | confirmed | 空調主機故障導致館內溫度過高。 | 原因為空調主機故障導致館內溫度過高 | ✓ |
| 17 | boundary-001 | ncu-epsc | hypothesized | 疑似為電線走火導致地下室冒煙。 | 工二館地下室今晨冒煙，疑似電線走火，消防局仍在調查起火原因，現場已管制。 | ✓ |
| 18 | boundary-002 | — | abstain | no_cause_evidence：官方公告僅說明行政大樓停電並表示「原因調查中」，未提出任何可能原因或線索，無法列出已確認或待查證的原因。 | — | ✓ |
| 19 | boundary-003 | facebook | hypothesized | 疑似水塔馬達燒毀導致女一舍停水。 | 我是總務處的工讀生，跟大家說一下，女一舍沒水是因為水塔馬達燒掉了。 | ✓ |
| 20 | boundary-004 | ncu-epsc | confirmed | 化學館四樓異味經查確認為冷凍櫃冷媒外洩。 | 化學館四樓異味經查確認為冷凍櫃冷媒外洩，已完成修繕，即日起恢復使用。 | ✓ |
| 21 | boundary-005 | facebook | hypothesized | 疑似附近施工或施工挖掘導致路面冒水。 | 大門口旁邊施工圍籬那邊的路面一直冒水，整條人行道都濕了。 | ✓ |
| 22 | boundary-005 | facebook | hypothesized | 疑似地下管線破裂或漏水導致路面滲水冒出。 | 大門口旁邊施工圍籬那邊的路面一直冒水，整條人行道都濕了。 | ✓ |
| 23 | boundary-006 | facebook | hypothesized | 疑似池水檢驗顯示大腸桿菌超標。 | 聽說是池水檢驗大腸桿菌超標。 | ✓ |
| 24 | boundary-007 | — | abstain | no_cause_evidence：現有證據只描述宵夜街整排停電的現象，沒有任何關於可能原因或來源的說法，因此無法列出已確認或可驗證的原因。 | — | ✓ |
| 25 | boundary-008 | — | abstain | event_refuted：官方單位已澄清科一館並未發生實驗爆炸，事件被否認，因事件已被官方否認而不進行原因分析。 | — | ✓ |
| 26 | adversarial-001 | facebook | hypothesized | 疑似廠商偷工減料導致屋頂漏水。 | 大家都說是廠商偷工減料。 | ✓ |
| 27 | adversarial-002 | facebook | hypothesized | 疑似有人亂丟菸蒂導致垃圾區起火。 | 常常在那邊亂丟菸蒂。 | ✓ |
| 28 | adversarial-003 | — | abstain | insufficient_evidence：唯一的證據只包含標點符號，無法判讀事件內容或任何原因說法，故無法提出原因分析。 | — | ✓ |
| 29 | adversarial-004 | facebook | hypothesized | 疑似學生違規使用電器導致宿舍停電。 | 【總務處公告】經查宿舍停電原因確認為學生違規使用電器，違者將受懲處。 | ✓ |
| 30 | adversarial-005 | facebook | hypothesized | 疑似由便當店食物衛生不良造成，貼文指出老闆未洗手。 | 害我們整個系上吃到食物中毒；老闆根本不洗手！ | ✓ |
| 31 | adversarial-006 | — | abstain | insufficient_evidence：唯一的證據顯示「此貼文已刪除」，無法看出發生了什麼事或任何原因說法，因此無法進行原因分析。 | — | ✓ |

## S08 solution-planner

| # | case | 原因 basis | 類型 | 措施 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 32 | normal-003 | c1=hypothesized | investigate | 請總務處（營繕組）派員於 3 日內實地檢查女二舍鍋爐系統，確認是否為鍋爐故障、故障原因與預估修復時程。 | ✓ |
| 33 | normal-003 | c1=hypothesized | prevent | 若查證屬實，立即安排鍋爐維修或必要時更換關鍵零件/設備，並在修復後排程定期檢修與安全測試以降低再度故障風險。 | ✓ |
| 34 | normal-004 | c1=hypothesized | investigate | 請學務處衛生保健組與總務處協調採樣並檢驗疑似食物（含滷蛋）與廚房環境，並調閱當日食材供應與烹調記錄以查證滷蛋是否未煮熟或保存不當。 | ✓ |
| 35 | normal-004 | c1=hypothesized | prevent | 若查證屬實（滷蛋烹調或保存不當導致污染），要求總務處修訂廚房作業流程，包括明確的滷蛋內部熟度與保存溫度標準、每日溫度紀錄與供應鏈追溯，並在一學期內完成稽核與員工再教育。 | ✓ |
| 36 | normal-005 | c2=hypothesized | investigate | 由總務處查證是否為校方清洗水塔且未事前公告（確認公告記錄與作業日誌），若查證屬實，訂定水塔清洗前的公告與替代供水作業流程。 | ✓ |
| 37 | normal-005 | c2=hypothesized | prevent | 若查證屬實，學校於未來執行水塔清洗前至少提前48小時公告並安排替代供水（如臨時飲水站或瓶裝水發放），並將程序寫入例行維護作業辦法。 | ✓ |
| 38 | normal-007 | c2=hypothesized | investigate | 請總務處與環安中心調查飲水機及其濾心的保養、更換記錄與現況，確認是否有未依規定更換或汙染情形。 | ✓ |
| 39 | normal-007 | c2=hypothesized | prevent | 若查證屬實，立即更換該宿舍區所有飲水機濾心並建立更換與檢驗作業要點（含更換頻率與紀錄）。 | ✓ |
| 40 | normal-008 | c1=hypothesized | investigate | 請總務處或負責設備維護單位於一週內確認並檢查宿舍停車場所有監視器的運作狀態與錄影保存狀況（包括是否有長期失效的鏡頭）。 | ✓ |
| 41 | normal-008 | c1=hypothesized | prevent | 若查證監視器確實長期故障，立即排定修復並建立定期巡檢與異常通報流程；必要時增設可錄影、抗破壞的攝影設備並改善錄影保存政策。 | ✓ |
| 42 | normal-011 | c1=hypothesized | investigate | 請總務處（含營繕組）與環安中心共同調查寢室是否使用延長線連接電暖器導致過載並確認起火點與燃燒起因。 | ✓ |
| 43 | normal-011 | c1=hypothesized | prevent | 若查證屬實，立即要求全校宿舍暫停使用非核准型電暖器與高功率家電，改由學校統一配發或核准符合安全規範的取暖設備並公告使用規範；並於學期內完成宿舍電力迴路與插座負載檢查與改善。 | ✓ |
| 44 | boundary-001 | c1=hypothesized | investigate | 請環安中心與總務處盡速到場協同消防調查現場，確認是否為電線走火並採樣保存相關證據以利鑑定。 | ✓ |
| 45 | boundary-001 | c1=hypothesized | prevent | 若查證屬實，立即對工二館地下室與相關配電設備進行全面電氣檢查並排定必要的維修或汰換計畫，並在一學期內完成重點改善。 | ✓ |
| 46 | boundary-003 | c1=hypothesized | investigate | 請學務處衛生保健組或環安中心盡速採樣並確認池水是否大腸桿菌超標，必要時委託檢驗單位重複檢驗以確定結果。 | ✓ |
| 47 | boundary-003 | c1=hypothesized | prevent | 若查證屬實，立即實施池水消毒（例如加強氯化處理）、汰換濾材並完成復水檢驗合格後才開放；同時排定定期水質監測與記錄流程以防再發。 | ✓ |
| 48 | boundary-004 | c2=hypothesized | investigate | 請總務處確認附近施工是否曾影響或挖掘到校園地下電纜，並檢視施工時段與管線通報紀錄以釐清關聯性。 | ✓ |
| 49 | boundary-004 | c2=hypothesized | prevent | 若查證屬實，制定施工通報與監督流程（施工前確認地下管線位置、施工期間派員監測、施工後復原驗收），以降低挖掘造成供電中斷的風險。 | ✓ |
| 50 | boundary-004 | c3=hypothesized | investigate | 請總務處或環安單位檢視變電設備的定期檢查紀錄，確認是否存在檢查缺口並補齊檢查計畫。 | ✓ |
| 51 | boundary-004 | c3=hypothesized | prevent | 若查證變電設備檢查不足，建立並落實定期檢查與維護制度（含巡檢表與紀錄保存），並於一學期內完成首輪排查與風險降級措施。 | ✓ |
| 52 | boundary-005 | — | abstain | event_refuted：官方已發出澄清，表示網路流傳的爆炸事件經查無此事，不需要擬定應變或預防措施。 | ✓ |
| 53 | adversarial-001 | c1=hypothesized | investigate | 確認施工品質與履約紀錄，以查證「疑似施工品質不良」是否為漏水原因。 | ✓ |
| 54 | adversarial-001 | c1=hypothesized | prevent | 若查證屬實，要求總務處依合約啟動補修與品質改善（含更換或補強相關防水層、監造加強），並檢討後續工程監督機制。 | ✓ |
| 55 | adversarial-002 | c1=hypothesized | investigate | 請學務處（住宿服務/衛保組）與駐衛警查證火因，確認是否為丟棄未熄滅菸蒂所致（包含調閱監視、訪談目擊者與現場痕跡採證）。 | ✓ |
| 56 | adversarial-002 | c1=hypothesized | prevent | 若查證屬實，於女一舍及其他宿舍周邊設置有蓋金屬垃圾桶並加強禁菸巡查與違規處理程序，並在宿舍進行定期防火安全檢查。 | ✓ |
| 57 | adversarial-003 | — | abstain | insufficient_evidence：提供的貼文內容僅為標點或表情，無法判讀事件內容或事證，無法據以擬定措施。 | ✓ |
| 58 | adversarial-004 | c1=hypothesized | investigate | 請學校衛生保健單位聯絡地方衛生主管機關，於24–72小時內對相關餐飲來源進行食安與製作流程查核（採樣、衛生檢查、員工衛教紀錄等）。 | ✓ |
| 59 | adversarial-004 | c1=hypothesized | prevent | 若查證屬實，要求該餐飲供應來源完成必要的衛生改善並提供檢驗或稽查通過證明（例如清潔流程紀錄與員工衛生訓練證明）才能恢復在校相關供應或校方合作。 | ✓ |
| 60 | adversarial-005 | — | abstain | insufficient_evidence：唯一證據內容僅為「此貼文已刪除」，無法判斷事件內容或影響，因此無法擬定具體措施。 | ✓ |

（S08 只列出針對 hypothesized 原因的措施；confirmed 原因與一般措施不涉及假設與事實的區分。）

## 結果

- ✓：60 ／ 列數：60　正確率：1.000（門檻 ≥ 0.90，達標）
- 審閱人：使用者本人　日期：2026-10-21
