# Alpha bot — audit aur kal ke changes ka blueprint

> **14 November update:** Atom ke live command responses ab review hue hain. Commands/leaderboard ke latest decisions ke liye [Atom-verified updated blueprint](Alpha-Bot-Atom-Verified-Blueprint-14-Nov-2026.md) use karein. Neeche ka original bug audit retained hai; iske UX proposals ko exact Atom behavior na samjhein.

**Audit:** 13 November 2026 · **Implementation target:** 14 November 2026, user ke saath next work session mein.

**Aaj ka scope:** code review, existing tests, temporary-database/mocked reproductions, aur GET-only Discord checks. Bot ka application code, live settings, roles, commands ya running process change nahi kiya gaya.

## 1. Audit ka result

**5 bugs reproduce hue.** Inke alawa **1 permission-policy gap**, **1 filter-coverage limitation**, aur commands/leaderboard ke kuch missing UX features verify hue.

- Existing suite: **156 tests pass**, Ruff lint pass; dependency ke 14 existing deprecation warnings.
- Additional audit probes: synthetic users, mocked Discord actions aur temporary SQLite databases mein run hue. Kisi real member ko test ke liye warn, timeout, reset ya role assignment nahi hua.
- Audit ke before/after **29 application/config/package files ke hashes same** rahe.
- Live configuration read-only check: moderation **observe**, text XP **on**, voice/reaction XP **off**, external AI **off**; OpenAI key configured nahi hai.
- Discord GET-only check: bot installed hai aur **26 top-level commands** registered hain. API installation check current Gateway uptime ka proof nahi hai.

Pichle version ke tests useful hain, lekin concurrent commands aur kuch edge cases unmein cover nahi the. Isliye passing tests ko complete correctness ya full Atom parity nahi kehna chahiye.

## 2. Confirmed bugs — pehle ye fix honge

### A01 · High priority: zero active warnings par escalation

**Source:** `aegis/service.py:102` — `warning_escalation`.

Current check warning count ka modulo use karta hai. Zero bhi threshold ka multiple ban jata hai, isliye helper zero active warnings par timeout request kar sakta hai.

**Reproduction:** active warnings `0`, helper result `timeout_applied`, mocked timeout calls `1`.

**Realistic trigger:** warn save hone aur escalation count read hone ke beech koi moderator warning void kar de, aur active count zero ho jaye. Ye har normal first warning par hone wala issue nahi hai; ye edge case hai. Audit ne kisi real member ke saath aisa incident establish nahi kiya.

**Kal ka fix:** zero/under-threshold count ko explicitly reject karna, warning/void/escalation ko same member-level coordination mein rakhna, aur duplicate escalation ko prevent karna.

**Acceptance:** zero warnings = kabhi timeout nahi; threshold par ek intended action; concurrent void se invalid timeout nahi.

### A02 · High priority: `/rank` aur reset ka race

**Sources:** `aegis/cogs/leveling.py:126`, `aegis/leveling.py:171`.

`/rank` currently sirf information nahi dikhata: woh reward roles sync bhi karta hai. XP snapshot read aur role sync ek coordinated member transaction mein nahi hain.

**Reproduction:** pehle rank request ne level-5 snapshot read kiya; parallel reset ne XP `0` aur roles empty kiye; purani rank request complete hote hi reward role wapas add ho gaya. XP phir bhi `0` tha.

**Kal ka fix:** `/rank`, `/statistics`, `/placement`, `/leaderboard` ko read-only banana. Role changes XP writes/explicit authorised sync se hon, aur reset/opt-out ke saath same member lock use karein.

**Acceptance:** rank aur reset parallel chalne ke baad XP zero aur unearned role absent rahe; viewing commands role APIs call na karein.

### A03 · Medium priority: simultaneous settings edits lose ho sakti hain

**Sources:** `aegis/level_store.py:95`, leveling reward/multiplier/ignore command handlers.

Do admins same old settings snapshot se alag reward tiers add karein to later full-map write pehli entry overwrite kar sakta hai. Database lock writes ko serialize karta hai, lekin command ka read–modify–write poora atomic nahi hai.

**Reproduction:** intended reward levels `[5, 10]`; final persisted levels sirf `[10]`.

**Kal ka fix:** repository mein atomic add/remove/patch operations; validation aur merge same lock/transaction mein. Same pattern reward maps, multipliers aur exclusion lists par apply hoga.

**Acceptance:** parallel different-tier additions dono preserve karein; same-key conflict predictable ho aur audit trail rahe.

### A04 · Medium priority: voice progress unnecessarily reset hoti hai

**Source:** `aegis/cogs/leveling.py:76`.

Voice channel mein kisi ka join/leave/mute event affected channel ke sab tracked members ke partial intervals clear kar deta hai. Do eligible members already continuously present hon, aur teesra join kare, tab bhi unka accumulated time chala jata hai.

**Reproduction:** pehle 2 eligible sessions, join ke baad 3 humans; original sessions remaining `0`.

**Kal ka fix:** eligibility per member re-evaluate karna. Jo members continuously eligible rahe aur minimum participants condition satisfy hoti rahe, unka interval preserve ho. Naye entrant ka timer alag start ho.

**Acceptance:** 59 seconds se present do members ko third member ke join se minute dobara start na karna pade. Solo, mute/deafen, AFK aur disconnect protections retained rahein.

**Current impact:** voice XP abhi disabled hai; enable karne se pehle fix/test karna zaroori hai.

### A05 · Medium priority: overlapping message events ka pending marker

**Sources:** `aegis/cogs/automod.py:59`, `aegis/leveling.py:274`.

Message create aur edit concurrently process ho rahe hon to dono same `pending_messages` set key use karte hain. Pehla event finish hote hi key delete ho jati hai, bhale doosra event moderation mein ho.

**Reproduction:** edit still processing `true`, message marked pending `false`.

**Kal ka fix:** reference count/per-message event coordination; marker tabhi clear ho jab us message ke saare in-flight checks settle hon. Edited content ke result ordering ka regression test bhi add hoga.

**Acceptance:** pending moderation ke dauran reaction XP grant na ho; exception/cancellation ke baad marker leak bhi na ho.

## 3. Permission aur filter follow-ups

### P01 · Reward-role permission policy inconsistent hai

**Sources:** `aegis/cogs/leveling.py:400`, `:506`, `:553`.

Reward mapping command Manage Server + Manage Roles maangta hai, lekin XP adjustment/reset only Manage Server check karte hain, jabki woh role changes trigger kar sakte hain.

**Reproduction:** synthetic caller ke paas Manage Server tha, Manage Roles nahi; reset permission check pass hua aur mocked role removal request hui.

Ye current staff/admin-role escalation ka proof nahi hai: existing role guards powerful reward roles ko reject karte hain, aur current milestone roles zero-permission hain. Lekin authority policy consistent honi chahiye.

**Recommended rule:** data/settings read ke liye Manage Server; reward-affecting administration, reset aur explicit role sync ke liye Manage Server + Manage Roles. Public member commands se writes nahi honge.

### F01 · Local phrase filter ki coverage limited hai

**Source:** `aegis/rules.py:15`.

Synthetic banned word `badword` aur `b.a.d.w.o.r.d` detect hue; `b@dword` detect nahi hua. `@ → a` substitution currently covered nahi hai.

Ye keyword filter ki verified limitation hai, general language understanding ka test nahi. External AI review bhi abhi off hai.

**Kal ka plan:** reviewed substitution cases, bounded obfuscation checks aur English/Hindi/Hinglish evaluation examples add karna. `bass`, `class` aur quoted/benign usage jaise false-positive cases saath test karna. Blanket substring blocking nahi lagani.

Mass mentions (`@everyone`/`@here`) ke liye separate configurable rule bhi backlog mein rakhein; current mention threshold users/roles ko count karta hai.

## 4. Deployment/security follow-ups

- **Token:** bot credential is chat mein share ho chuka hai. Maintenance mein rotate karke replacement sirf local `.env`/host secret mein update karna; report ya chat mein token nahi likhna.
- **Permissions:** GET-only audit mein **🤖 BOTS** aur **Alpha bot**, dono roles Administrator grant kar rahe the. Sirf ek role se Administrator remove karna enough nahi hoga. Dedicated Alpha permissions aur channel overrides verify karke access narrow karna; shared BOTS role ko blindly edit karke doosre bots affect nahi karne.
- **Uptime:** current launch foreground/local process hai. Auto-start, process supervisor aur heartbeat/last-seen health abhi reliable deployment feature ke roop mein set nahi hain. PC sleep/crash ko normal Gateway reconnect solve nahi karta.
- **Role/announcement failures:** counters milte hain, lekin durable retry/reconciliation queue aur clear partial-outcome reporting improve karni hai. XP grant ko notification failure par rollback nahi karna.

In follow-ups mein aaj koi credential, permission ya hosting change nahi hua.

## 5. Atom-jaisi command familiarity ka plan

Atom ki public command list mein `/rank`, `/leaderboard`, `/xp_needed`, `/statistics`, `/time`, `/placement`, `/manage`, `/config`, `/help` aur `/customize` documented hain. Public page complete argument/subcommand syntax establish nahi karta. Neeche root names reference se match kiye gaye hain; detailed Alpha syntax proposed hai. [Atom official commands](https://www.leveling.gg/commands)

| Current Alpha | Proposed familiar command | Member/admin experience |
|---|---|---|
| `/rank [member]` | `/rank [member]` | Same familiar entry; view-only card and XP progress |
| `/leaderboard period page` | `/leaderboard` | Interactive board; manual page number type karna optional |
| `/xp-needed level` | `/xp_needed level` | Atom-style canonical spelling; old name compatibility alias rahe |
| Rank ke andar limited counters | `/statistics [member]` | XP breakdown aur clearly labelled activity counts |
| Separate time command nahi | `/time [member]` | Current voice session + observed total/eligible time, jab tracking available ho |
| Separate placement nahi | `/placement [member] [period]` | Top 10 ke bahar ho tab bhi apni position dikhe |
| `/levels xp`, `/levels reset` | `/manage xp add/remove/set`, `/manage level set/reset` | One admin entry, reason/confirmation, consistent permissions |
| `/levels ...` | `/config leveling ...` | Configuration discovery ek familiar root ke neeche |
| `/help` | `/help` | Member/admin sections aur real command examples |
| Theme selection nahi | `/customize` | Later phase: approved rank-card themes/accent choices |

**Compatibility:** old `/levels`, `/xp-needed` aur existing moderation commands turant delete nahi karne. Aliases same service functions aur same permissions use karein. Pehle names/help migrate hon; XP, warnings aur reward mappings preserve rahein.

`/manage level set` same XP curve se target level ka XP calculate kare; permission bypass ya hidden reset nahi. Destructive actions explicit confirmation aur reason maangein.

## 6. Leaderboard blueprint — members ke liye familiar flow

**Current:** private text embed, 10 entries, period/page arguments. Previous/Next buttons, member position shortcut, source filters aur live/pinned board nahi hain. Isliye abhi leaderboard ko Atom ke barabar kehna accurate nahi hoga.

Atom officially leaderboard, placement aur daily/weekly/monthly live ranking features describe karta hai. Exact current Discord button layout/argument behavior public pages se verify nahi hua; neeche proposed Alpha UI hai. [Atom leaderboards](https://www.leveling.gg/features/leaderboards)

```text
ALPHA · PRIME GAMING COMMUNITY
LEADERBOARD                         Weekly · Last 7 days

Period:   [ All time | Daily | Weekly | Monthly ]
Activity: [ Total XP | Text XP | Voice XP | Reaction XP ]

 #     Member / avatar         Level        XP
 1     Nova Rook                12           8,420
 2     Pixel Storm              11           7,960
 3     Sky Runner               10           6,840
 ...   10 entries per page

Your placement: #27 · 2,340 XP in this period

[ First ] [ Previous ]  Page 1 / 8  [ Next ] [ Last ]
[ My position ] [ Refresh ] [ How XP works ]

Updated just now · Weekly means the last 7 days
```

Names aur values illustration ke liye fictional hain.

**Behavior:**

1. Default entry `/leaderboard`; familiar selectors member ko period/source choose karne dein.
2. First/Previous/Next/Last buttons; invalid pages aur disabled edges handle hon.
3. My position top-10 se independent ho; rank, placement aur leaderboard same scoring/tie rules use karein.
4. Public leaderboard dedicated bot-commands channel mein; elsewhere private default/explicit visibility option. Public controls ke concurrent clicks ek doosre ka view unexpectedly replace na karein.
5. Empty state mein earning instructions; no-data period ko application failure nahi dikhana.
6. No mass mentions. Read-only button callbacks, rate limits aur bounded image/cache work.
7. Existing rolling windows ko honestly label karein: Daily = last 24 hours, Weekly = last 7 days, Monthly = last 31 days. Calendar-reset seasons alag later setting hogi; Atom ke exact reset boundaries assume nahi karne.
8. Equal-XP display/tie policy decide karke har screen par consistently apply karna. Internal ordering stable rahe.
9. Optional pinned/live board later: one configured message ko debounced updates; every award par new message spam nahi.

Rank card mein consistent Alpha colors, level/XP bar, avatar, next reward aur readable names hon. Atom custom backgrounds/layouts support karta hai; Alpha theme choices planned improvement hain. Portable open-license fonts bhi add karne hain, taaki Linux host par Hindi names `?` mein degrade na hon. [Atom rank-card reference](https://www.leveling.gg/features/rank-cards)

## 7. Missing statistics ko sahi tarah add karna

Current `text_awards` total chat messages nahi hai; ye XP-awarded messages hai. Current `voice_awards` actual voice duration nahi hai; ye awarded eligible minutes hai. Inko bas `/statistics` ya `/time` mein rename karke total activity claim nahi karna.

Proposed separate fields:

- Tracked valid message count, including messages during XP cooldown; XP-awarded message count separately.
- Observed connected voice time aur XP-eligible voice time separately.
- Unique eligible reactions received aur XP-awarded reactions separately.
- Text/voice/reaction XP aur signed admin corrections ka breakdown.

New activity tracking start date dikhegi. Historical counts invent/backfill nahi honge. Schema v3 additive migration, backup aur privacy/retention update chahiye. Existing v2 XP totals reset nahi honge.

## 8. Kal ka implementation order

| Order | Work | Done kab maana jayega |
|---|---|---|
| 1 | Backup, credential/permission review; A01–A05 regression cases | Reproduced bugs fail-before/fix-after tests se cover hon |
| 2 | Warning/rank/config/event/voice fixes + role permission consistency | Existing 156 tests aur new regression tests pass; no unexpected role/timeouts |
| 3 | Familiar command names/aliases + help | Old commands still work; new commands same authorization use karein |
| 4 | Interactive leaderboard + placement | Buttons, period/source consistency, concurrency, empty states aur access tests pass |
| 5 | Statistics/time storage, if included in that session | Additive migration preserves XP/cases; labels actual recorded data match karein |
| 6 | Staging checks, then controlled deployment | Bot connects, commands sync once, backup restore path verified, one process only |

**First delivery ka target:** correctness fixes + familiar member commands + usable leaderboard. Dashboard, economy/inventory, giveaways/drops, achievements, external imports, arbitrary background uploads aur extra games separate backlog hain. Missing optional features ko working command ki tarah advertise nahi karna.

Voice/reaction XP ka enable/disable aur moderation observe/enforce decision bug fixes se alag rahega; upgrade in settings ko silently flip nahi karega.

## 9. Verification checklist

- [ ] Zero active warnings never timeout; void/escalation race covered.
- [ ] `/rank` and other display commands make no role changes.
- [ ] Reset + concurrent rank cannot restore an unearned role.
- [ ] Parallel settings additions preserve different entries.
- [ ] Existing eligible voice members retain progress when a third joins.
- [ ] Pending create/edit checks remain marked until both settle.
- [ ] Role-affecting admin commands follow the agreed permission rule.
- [ ] Obfuscation coverage improves without substring false positives.
- [ ] XP values/placement match across every view and period/source filter.
- [ ] Buttons enforce access, rate limits and safe multi-user behavior.
- [ ] v2 data survives any v3 migration; no score/history reset.
- [ ] Live member flow is checked in an agreed test area; mock tests alone are not called end-to-end verification.
- [ ] Uptime/last-seen status distinguishes installed, connected and actually processing.

## 10. Evidence summary

| Mock/temporary check | Observed result |
|---|---|
| Zero active warnings | Timeout branch requested once |
| Concurrent reward additions for levels 5 and 10 | Only level 10 survived stale map write |
| Third member joins an already eligible voice pair | Both original partial sessions cleared |
| Rank snapshot finishes after reset | XP 0, reward role restored from old snapshot |
| Manage Server without Manage Roles | Reset permission check passed and role removal requested |
| Create finishes while edit still running | Pending marker false although edit was in flight |
| Harmless synthetic word substitution test | Plain/punctuation variants detected; `@` replacement missed |

These results establish code paths under controlled conditions, not a claim that real server members were already affected. Today’s work produced this audit/blueprint only; implementation is deferred to the next session as requested.
