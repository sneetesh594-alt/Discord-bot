# Alpha bot — Atom live review aur updated change blueprint

**Original audit:** 13 November 2026  
**Atom live review:** 14 November 2026  
**Status update — 14 November:** v1.2 core code/build complete, 183 tests pass; bot online aur 32 commands synced hain. Real-member smoke checks pending hain. [Implementation report](Alpha-Bot-v1.2-Implementation-Report.md). Neeche reference-review findings aur original acceptance scope preserve hain.

Ye document pehle blueprint ke command/leaderboard proposals ko update karta hai. Original bug evidence [13 November audit](Alpha-Bot-Audit-Blueprint-14-Nov-2026.md) mein hai. Us report ke generic XP-source filters aur navigation wireframe ko exact Atom behavior na samjhein; neeche live-verified distinctions hain.

## 1. Atom mein actual kya check hua

Prime Gaming Community ke private **bot-work** channel mein Atom ke eight member-command flows ke responses dekhe gaye. Ye live UI observations hain; documentation-only claims se alag hain.

| Flow | Observed response | Evidence |
|---|---|---|
| `/leaderboard xp` | Up to 10 ranked entries; username, level, purple progress bar aur percentage. Free-server footer mein Top 100 link; paginated browsing premium ke roop mein advertised. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570982155163541604) |
| `/leaderboard text` | Ranking by **message count**, source se earned XP nahi. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570984519819858010) |
| `/leaderboard voice` | Ranking by **voice minutes**. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570984704075759656) |
| `/leaderboard reaction` | Default received-reaction ranking. **Given / Received** selector ka callback same message ko edit karta hai; Given select karne par members, counts aur order badle. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570984828684341308) |
| `/rank` | PNG card: banner, avatar, display name/username, server placement, current/next level, cumulative XP aur next-level progress. Test account initially level 0, 77 XP, progress 77/100, placement #17 tha. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570982401918894190) |
| `/statistics` | Separate messages, voice minutes aur reactions; historical-data cutoff notice bhi visible tha. Test account ke values us waqt 3 / 0 / 0 the. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570984277284356188) |
| `/xp_needed` | Target na dene par next level; same account ke liye 23 XP remaining, jo 77/100 card se match hua. Configured text/voice earning estimates bhi aaye. | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570985006808178738) |
| `/time` | Voice mein na hone ya eligibility fail hone ka message. **Eligible active-session timer test nahi hua.** | [Discord response](https://discord.com/channels/1483405540666376314/1558439084924473364/1570985292935204966) |

**Incidental test effect:** `/placement` inspect karte waqt ek entry actual slash interaction ke bajay literal chat text chali gayi. Atom ne ordinary activity XP diya aur test account ka level-1 announcement dikha. Isliye review ko “Atom XP bilkul unchanged” nahi kehna chahiye. Koi administrator XP adjustment ya rollback nahi kiya. Successful `/placement` lookup establish nahi hua.

XP board mein kuch bahut high pre-existing levels bhi the. Unka cause investigate nahi hua; unhe bug ya natural earning ka proof nahi maana gaya.

### Dashboard ki verified screens

- Normal **Leaderboard** aur premium **Live Leaderboard** alag sections hain. Normal settings mein former members ko show/hide karne ka control hai; unka XP/stats preserve karne ki explanation hai. Custom URL premium tha.
- Rank Card V2 aur color controls visible the. Is server ke free plan par V1, banner customization aur gradient controls locked the.
- Announcements mein target channel, content/embed delivery, member/level variables, embed fields aur level overrides visible the.
- Channel multiplier page mein empty state aur configured-count limit visible thi; rank-position rewards premium gate ke peeche the.
- Cooldown controls dekhe gaye, lekin unke dono labels/units navigation se pehle capture nahi hue. Exact values ko Alpha defaults ki tarah copy nahi karna.
- Reset configuration, reset leveling aur reset statistics separate actions ke roop mein dikhte hain. Reset controls activate nahi kiye gaye.

Maine Atom ya Alpha ke server settings save nahi kiye. Testing se Discord responses aur reaction-board view update hue; upar wala ordinary-message XP effect hua.

## 2. Pehle ka blueprint kahan correct karna hai

1. **Activity leaderboard = source-XP leaderboard nahi.** Text means messages, voice means minutes, reaction means action counts. XP earned by text/voice/reactions ek separate statistics breakdown hoga.
2. **XP leaderboard ka familiar visual level + progress hai.** Sirf total-XP ki text list Atom jaisa member experience nahi deti.
3. **Free Atom mein Previous/Next buttons verify nahi hue.** Response mein pagination premium advertise hui. Alpha mein buttons dena proposed improvement hai; ise observed free-Atom parity na likhein.
4. **Reaction ranking mein Given aur Received dono chahiye.** Reaction XP kisko milega, ye alag earning policy rahegi.
5. **Rank aur stats ke historical data ki meaning clear honi chahiye.** Current Alpha counters ko rename karke total activity ka claim nahi kar sakte.

## 3. Alpha ki confirmed mistakes aur priority

13 November ke controlled probes mein **5 bugs reproduce hue**, aur existing suite ke **156 tests / Ruff checks pass** the. Ye historical audit results hain; is UI-review session mein suite dobara run nahi ki gayi.

| Priority | Finding | Required fix |
|---|---|---|
| P0 | Zero active warnings par modulo check timeout branch allow karta hai. | Zero/under-threshold guard; warning, void aur escalation coordinate karna; duplicate escalation block. |
| P0 | Purana `/rank` snapshot reset ke baad reward role wapas add kar sakta hai. | Display commands read-only; XP/reset/role-sync same member coordination use karein. |
| P1 | Simultaneous settings edits stale maps overwrite kar sakti hain. | Atomic repository patch/add/remove operations. |
| P1 | Third voice participant ke join par continuously eligible pair ka partial interval clear hota hai. | Per-member eligibility transitions; unaffected intervals preserve. Voice XP abhi off hai. |
| P1 | Overlapping create/edit moderation checks pending marker jaldi clear kar dete hain. | Per-message coordination/reference count; final in-flight check ke baad clear. |

**Permission gap:** XP adjustment/reset role changes trigger karte hain, lekin reward-mapping command jaise Manage Roles checks consistent nahi hain. Recommended admin policy: reward-affecting operations ke liye Manage Server + Manage Roles, role-safety validation aur reason/audit trail.

**Display-label defect:** `aegis/cogs/leveling.py:150-151` currently XP-awarded messages ko `Messages`, aur awarded voice ticks ko `Voice minutes` kehta hai. Pehle labels ko “XP-awarded messages” / “XP-credited minutes” karna; actual activity counters alag add karna.

**Filter limitation:** reviewed synthetic `@` substitution miss hua. Obfuscation coverage improve karni hai, benign words/quoted content ke false-positive tests ke saath.

Original report ke credential rotation, dual Administrator-granting bot roles, role hierarchy, durable delivery retries aur reliable hosting follow-ups abhi bhi relevant hain. Existing shared bot role ko bina dependency review ke edit nahi karna.

## 4. Commands — familiar names aur honest compatibility

Live menus ne rank/statistics member option, xp_needed ke optional level/member, aur placement ke required leaderboard + optional member ko confirm kiya. Administration ke documented XP verbs mein **subtract** hai. Detailed reference documentation ka verification date August 2026 hai; unexecuted administration ko live-tested na kahen. [Atom detailed command reference](https://www.leveling.gg/guides/reference/commands)

| Proposed Alpha command | Expected behavior |
|---|---|
| `/rank [member]` | View-only rank card; default caller; level, placement, XP/progress aur next milestone. |
| `/leaderboard xp [period] [page]` | XP ranking with level/progress; default all time, first page. |
| `/leaderboard text [period] [page]` | Tracked eligible message counts. |
| `/leaderboard voice [period] [page]` | Tracked credited voice minutes. |
| `/leaderboard reaction [period] [page]` | Received default; Given/Received selector. |
| `/statistics [member]` | Activity counts, source XP, admin corrections aur tracking start date clearly separated. |
| `/xp_needed [level] [member]` | Default next level; optional target/member; remaining XP and bounded earning estimates. |
| `/time` | Caller ka current observed voice session aur eligibility explanation. Optional member lookup later Alpha extension ho sakta hai. |
| `/placement leaderboard:<period> [member]` | Chosen Alpha board mein position, including members outside the first page. Atom ke custom live-board IDs ke equivalent hone ka claim nahi. |
| `/manage xp set/add/subtract` | Proposed Alpha grouping; audited adjustment, correct permissions; exact option handling implementation mein define hoga. |
| `/manage level set/reset` | Proposed Alpha extension; current XP curve, explicit reason/confirmation. |
| `/config leveling ...` | Proposed Alpha settings grouping; existing `/levels` maintained. |
| `/help [command]` | Member/admin discovery, examples, and explanation of units/cooldowns. |

**Slash-command migration:** current flat `/leaderboard period page` aur a new `/leaderboard xp/text/voice/reaction` group ek hi Discord root par simultaneously preserve nahi ho sakte. Group adopt karte waqt old flat syntax ka migration explain karna. Same XP service ke liye distinct legacy entry, jaise proposed `/xp-leaderboard period page`, diya ja sakta hai. Bare `/leaderboard` group ko executable default response ki tarah promise nahi karna; help mein `/leaderboard xp` entry dikhani hai.

Old `/levels`, `/xp-needed` aur moderation commands preserve honge. Shared handlers/authorization use hon; XP/cases/reward maps reset nahi honge. Display command ke andar hidden role synchronization nahi hoga. Explicit role-update action ko separate, authorized flow milega.

## 5. Leaderboard aur rank-card design

### XP board — observed structure se familiar

```text
ALPHA · XP LEADERBOARD
Prime Gaming Community · All time

#1  [Lvl. 12]  Member A
    ███████░░░  70% toward the next level

#2  [Lvl. 11]  Member B
    ████░░░░░░  40% toward the next level

... up to 10 members per page

[Previous]  Page 1 / 8  [Next]
[My position] [Refresh]
```

Example names/levels fictional hain. Progress percentage current level ke andar progress hai, server XP share nahi. XP amount optional extra row ho sakta hai; level/progress primary recognition rahe.

### Activity boards

- **Text:** rank, member, messages.
- **Voice:** rank, member, minutes; detailed view mein hours/minutes formatting.
- **Reaction:** rank, member, reactions; Given/Received dropdown. Mode switch same message/view ko update kare, new messages spam na kare.
- Header mein metric aur period explicit ho; missing activity ko source XP se fill nahi karna.

**Alpha additions:** pagination, My position, Refresh, period selection aur clear empty states. Ye useful enhancements hain, exact observed Atom-free controls ki copy nahi.

**Periods:** existing Alpha rolling windows honestly label hon: last 24 hours / 7 days / 31 days. Atom ke calendar reset boundaries verify nahi hue. Calendar seasons later explicit timezone/reset rules ke saath add hon.

**Consistency:** leaderboard, rank, placement aur stats same scoring/tie policy use karein. Public controls ke multi-user clicks ek doosre ka private view replace na karein; callback ownership, expiry, rate limits aur edge pages handle hon. No mass mentions.

**Web board:** Atom ka Top 100 link response mein visible tha; linked public page is review mein open/test nahi hui. Alpha ka hosted public board, vanity URL aur live pinned board separate deliverables hain.

**Membership:** former-member visibility ki explicit setting chahiye. Hide ka matlab XP delete nahi. Reliable membership observations ke bina “current members only” ka promise nahi karna.

**Rank card:** banner/avatar, readable member identity, placement, current/next level, accumulated XP aur progress. Portable Hindi/Unicode fonts, long names and missing avatars test hon. Alpha artwork/branding apna ho. Atom's official V2 guide bhi these core data fields describe karta hai. [Rank-card guide](https://www.leveling.gg/guides/build/rank-cards)

## 6. Data work — four boards ko real banane ke liye

True activity storage initial delivery ka prerequisite hai, agar text/voice/reaction boards bhi ship karne hain.

- `tracked_messages` aur `xp_awarded_messages` separate: cooldown ke beech valid messages ka activity count policy clear ho; bots, webhooks, exclusions aur command-only messages define hon.
- Connected/observed voice duration, credited activity minutes aur XP-eligible minutes separate. Restart ke offline time ka backfill nahi; continuously eligible members ka partial interval preserve ho.
- Reactions given/received ke unique event attribution, duplicates, self/bot reactions, removals aur re-addition policy define karna. Activity counters ko XP grants se independent rakhna; XP farming prevention retained rahe.
- Source XP aur signed administrator adjustments alag totals; admin correction ko message/voice/reaction event count na banana.
- Per-period boards ke liye timestamped activity aggregates/events, tracking start date, retention aur opt-out deletion consistent hon.

Schema v3 additive migration: v2 XP, warning/case history aur reward maps preserve. Existing XP-award counts migrate ho sakte hain as award counts; unse historical total messages ya actual voice time invent nahi karna. New stats “tracking started …” show karein. XP opt-out ke saath new activity tracking/deletion policy bhi update hon.

XP-needed estimates configured ranges/multipliers se aayein, guaranteed wall-clock time ki tarah present na hon. Voice XP disabled ho to members ko voice se XP earn karne ka estimate na dikhayein. Atom's XP rules and Alpha's chosen curve/rates identical assume nahi karne. [Text XP rules](https://www.leveling.gg/guides/build/text-xp), [voice eligibility rules](https://www.leveling.gg/guides/build/voice-xp)

## 7. Implementation order aur acceptance

| Order | Deliverable | Acceptance |
|---|---|---|
| 1 | Backup + critical bug/permission fixes | Original five probes covered by regressions; zero-warning timeout blocked; reset/rank cannot restore old roles; concurrent changes preserved. |
| 2 | Correct labels + additive activity storage | XP/cases survive migration; no invented history; counters idempotent; opt-out/retention tested. |
| 3 | Familiar command names and compatibility | Correct app syntax, defaults, permissions; old supported names work; leaderboard syntax migration documented. |
| 4 | XP card/board, three activity boards, statistics/placement | Correct units, Given/Received updates, stable ordering, personal placement outside top 10, empty/expired states and safe multi-user controls. |
| 5 | XP-needed / voice-session view | XP-needed matches rank curve; disabled source estimates omitted; eligible/ineligible voice branches tested with observed sessions. |
| 6 | Staging and controlled deployment | Backup restore verified; one bot process, commands sync, real member flow and delivery verified; installed/connected/processing health distinguished. |

Text/voice/reaction XP switches aur moderation observe/enforce mode independently preserve honge. This research session ne Alpha upgrade deploy nahi kiya.

**Later backlog:** configurable calendar/live boards, hosted dashboard/public board, card themes, richer announcements, achievements, scheduled multipliers, imports, economy/crates/trivia. Atom mein module visible hone se Alpha mein uska command advertise nahi karna. Optional systems ko core correctness fixes ke saath mix karke delivery unclear nahi karni.

## 8. Verification limits

- Eight command flows ke responses observed; reaction selector ke two data views verified.
- `/time` ka successful eligible session, `/placement` ka real live-board lookup, Top 100 destination, premium pagination/live-board operation, admin XP/role changes, imports/resets aur paid modules verify nahi hue.
- Kisi UI label ya documentation page ko successful end-to-end operation ka substitute nahi maana gaya.
- Exact XP formula, existing unusually high levels ka cause, reaction removal accounting, multi-admin concurrency aur calendar reset policy Atom ke liye establish nahi hue.
- Core Alpha fixes/features ka current status linked implementation report mein hai. Live connection/command sync verified; member-flow verification pending hai; historical review ke claims unchanged hain.
