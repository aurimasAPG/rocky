# Rocky — kaip naudotis

Rocky — „OpenRouter“ **agentų orkestratorius**. Duodi tikslą ir katalogą. Jis iš gyvo modelių katalogo parenka modelius, suskaido darbą, naudoja įrankius ir patikrina rezultatą. Numatytosios išlaidų lubos — **1 EUR vienam paleidimui**.

Komanda — `rocky`.

Angliškas vadovas su tuo pačiu turiniu: `rocky help` (failas `rocky/HELP.md`).

---

## 1. Vienkartinis diegimas

Viena eilutė — įdiegia `uv`, jei jo nėra, ir Rocky kaip izoliuotą įrankį:

```bash
curl -fsSL https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.sh | sh
```

Windows (PowerShell): `irm https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.ps1 | iex`

Atnaujinti = paleisti tą pačią eilutę dar kartą. Pašalinti = `uv tool uninstall rocky`. Patikrinti = `rocky --version`.

Kūrėjams iš klono: `uv tool install --force --reinstall .` (po kiekvieno kodo pakeitimo paleisti iš naujo). Į `PATH` įtrauk `~/.local/bin`.

Raktą laikyk faile `~/.env` (niekada neįrašyk į git):

```
OPENROUTER_API_KEY=sk-or-...
```

Veikia ir projekto `.env`, ir aplinkos kintamasis. Rakto dar nėra? Tiesiog paleisk `rocky` — terminale jis paprašys rakto (LT / EN), įrašys jį į `~/.env` su teisėmis 600 ir iškart paleis `rocky doctor`.

```bash
rocky doctor
```

patikrina mašiną: Python versiją, ar raktas yra ir ar „OpenRouter“ jį priima, `~/.env` teises, ar darbo katalogas rašomas, smėlio dėžės režimą, Playwright + Chromium, rastus įgūdžius ir darbuotojo modelį, patikrintą gyvame kataloge (neegzistuojantis `ROCKY_WORKER` — `!`, nes paleidimai tyliai grįžtų prie pigiausio tinkamo modelio). `✓` — gerai, `!` — veikia, bet galėtų būti saugiau, `✗` — būtina sutvarkyti. Jei yra bent vienas `✗`, išeities kodas 1.

**Python 3.13:** **nenaudok** `pip install -e .`. Paslėpti `.pth` failai praleidžiami ir `rocky` neimportuos. Diek įprastai arba per `uv tool install`, kaip aukščiau.

Neprivalomi naršyklės įrankiai (Playwright + Chromium):

```bash
uv tool install --force --reinstall ".[browser]"
playwright install chromium
```

---

## 2. Pirmas paleidimas

```bash
cd /kelias/iki/projekto/kuri/nori/redaguoti
rocky
```

Turėtum pamatyti auksaspalvio retriverio plakatą. Įrašyk užduotį, spausk Enter.

Vienu kartu:

```bash
rocky -d /tmp/rocky-demo "parašyk trijų sakinių santrauką į deliverables/summary.md"
```

Interaktyvi trijų langų žiūryklė:

```bash
rocky tui -d /tmp/rocky-demo
```

Šį vadovą (angliškai) perskaityti bet kada:

```bash
rocky help
```

---

## 3. Komandos

| Komanda | Ką daro |
| --- | --- |
| `rocky` | Interaktyvi CLI. Įrašai tikslą, stebi ciklą, rašai kitą. |
| `rocky "tikslas"` | Paleidžia tą tikslą kaip **foninį darbą** ir prisijungia. |
| `rocky tui` | Tas pats variklis, trys langai (planas / darbas / tikrinimas). |
| `rocky tui --attach ID` | Atveria TUI ant esamo paleidimo. |
| `rocky ps` | Šio katalogo paleidimų sąrašas: būsena, **išleisti €**, kada baigėsi, tikslas ir galutinio failo kelias (arba baigiamoji santrauka). |
| `rocky cost` | Kiek šis katalogas išleido: iš viso, šiandien, per 7 dienas, vienam naudojamam rezultatui, kiek nuėjo nieko nedavusiems paleidimams, trys brangiausi paleidimai. |
| `rocky attach` | Vėl prisijungti prie naujausio paleidimo. |
| `rocky attach ID` | Prisijungti prie to paleidimo (užtenka pradžios). |
| `rocky resume [ID]` | Pratęsti `unmet` / `error` / `crashed` / `stopped` paleidimą iš išsaugoto plano ir failų. Be ID — naujausią tokį. |
| `rocky stop [ID]` | Nutraukti vykstantį darbą dabar. Jau įrašyti failai lieka; paleidimas tampa `stopped` ir gali būti pratęstas. Be ID — naujausią paleidimą. |
| `rocky resume [ID] "ką pakeisti"` | Tęsti nuo **bet kurio** baigto paleidimo, įskaitant pavykusius: ta pati sudėtis, tas pats failas, be naujo įvertinimo / kritikos; planuotojas pradeda nuo tavo nurodymo. |
| `rocky open [ID]` | Atidaro paleidimo galutinį failą sistemos programa ir išspausdina jo kelią. Be ID — naujausią paleidimą. |
| `rocky serve` | Komandos tinklo sąsaja (numatytai http://127.0.0.1:8787). |
| `rocky route "tikslas"` | Tik įvertina ir parenka modelius. Be įrankių, be failų. |
| `rocky "tikslas" --json` | Be gyvo vaizdo: atlieka iki galo ir spausdina **vieną JSON objektą** (būsena, galutinis failas, įvertis, kaina). Į klausimus atsako saugiu numatytuoju atsakymu. Išeities kodas 0 = pavyko. |
| `rocky config` | Parodo Rocky skaitomus konfigūracijos failus, galiojantį biudžetą / ciklus / darbuotoją / kalbą ir pavyzdinį failą, jei jo nėra. |
| `rocky doctor` | Patikrina raktą, „OpenRouter“ pasiekiamumą, katalogą, smėlio dėžę, naršyklę, įgūdžius. Išeities kodas 1, jei kas nors sugedę. |
| `rocky help` | Angliškas vadovas. `rocky --help` viename ekrane išvardija komandas ir parametrus. |

---

## 4. Parametrai

| Parametras | Numatytoji reikšmė | Reikšmė |
| --- | --- | --- |
| `-d`, `--dir` | dabartinis katalogas | Darbo katalogas. Failų įrankiai gali liesti tik failus jame. |
| `--budget` | `1.00` | Šio paleidimo lubos **eurais**. `resume` be šio parametro palieka senojo paleidimo lubas. |
| `--max-loops` | `4` | Ciklų planas → darbas → tikrinimas skaičius. |
| `--model` | katalogo numatytasis | Šio paleidimo darbuotojo modelis („OpenRouter“ ID, pvz. `deepseek/deepseek-v4.1-flash`). Tas pats kaip `ROCKY_WORKER`. |
| `--lang` | užduoties kalba | Galutinį failą rašyti šia kalba (`lt`, `en`, …), kad ir kokia kalba parašyta užduotis. Tas pats kaip `ROCKY_LANGUAGE`. |
| `--no-server-tools` | išjungta | Nenaudoti „OpenRouter“ serverio interneto įrankių. |
| `--host` / `--port` | `127.0.0.1` `8787` | Tik `serve`. |
| `--attach` | tuščia | TUI: prisijungti prie esamo paleidimo ID. |
| `--json` | išjungta | Be gyvo vaizdo; galutinį rezultatą spausdinti JSON formatu (skriptams, CI, kitiems agentams). |

**Kitas** paleidimas vėl turi 1 EUR lubas, nebent vėl nurodai `--budget` — arba numatytąją reikšmę įrašai į konfigūracijos failą.

### Konfigūracijos failas

`~/.rocky/config.toml` laiko numatytąsias reikšmes visiems paleidimams; `<darbo katalogas>/.rocky/config.toml` jas perrašo projektui. Komandinės eilutės parametras nugali abu, o aplinkos kintamasis nugali failą.

```toml
budget = 3.0                          # EUR lubos vienam paleidimui
max_loops = 4
worker = "google/gemini-3.8-flash"    # „OpenRouter“ ID
language = "lt"                       # priverstinė galutinio failo kalba; praleisk, kad sektų užduotį
sandbox = "sandbox-exec"              # macOS; "docker" arba "none"
# token = "team-secret"               # rocky serve
# workspaces = ["/Users/me/projects"] # rocky serve
```

Kiti raktai: `image_model`, `sandbox_image`, `skills`, `recipes`, `bash_deny`, `base_url` — kiekvienas atitinka tos pačios reikšmės `ROCKY_*` kintamąjį. `rocky config` parodo, kurie failai skaitomi ir ką paleidimas naudotų; `rocky doctor` praneša apie sugadintą failą.

---

## 5. Kas vyksta paleidus

1. **Įvertinimas** — pigus modelis: sudėtingumas (`trivial` / `moderate` / `hard`), sritis, galutinis failas ir užduoties **kalba** (`lt`, `en`, …; jei modelis nenurodo, ją nustato euristika). Kiekvienam tolesniam vaidmeniui liepiama galutinį failą, santraukas, pastabas ir bet kokį klausimą tau rašyti ta kalba, nebent užduotis prašo kitos — lietuviška užduotis gauna lietuvišką failą, o ne tą kalbą tikrintojas laiko blokuojančiu trūkumu. Biudžeto klausimas lietuviškoms užduotims užduodamas lietuviškai (`taip` / `ne` / skaičius).
2. **Biudžeto patikra** — jei spėjama kaina viršija lubas, Rocky klausia, ar pakelti jas **tik šiam paleidimui**. Atsakyk `yes`, `keep` arba skaičių (`3`, `8`).
3. **Sudėtis** — iš gyvo „OpenRouter“ katalogo. Planuotojas (numatytai **Claude Opus 5.5**; jei jo kataloge nėra — stipriausias dabartinis modelis), kritikas (stipriausias modelis, ne brangesnis už planuotoją), darbuotojas (numatytai **Gemini 3.8 Flash** — lietuvių kalba + įrankiai; keičiama per `ROCKY_WORKER`), tikrintojas (kita modelių šeima; tik skaitymo įrankiai; stipriausias modelis iki 5 / 8 / 12 $ už milijoną mišrių tokenų kainos lubų trivial / moderate / hard tikslams, nes jis per kelis ėjimus iš naujo skaito galutinį failą ir jo įvesties tokenai lemia kainą — pirmasis invohub paleidimas mokėjo 1,50 € už patikrą prie 18 $/M). Automatiškai parenkant praleidžiami modeliai, kuriems katalogas nurodo pabaigos datą, ir modeliai, išleisti daugiau kaip 90 dienų prieš naujausią savo šeimos modelį, kad didesnė kaina nesuteiktų pirmenybės ankstesnei kartai. Tavo prisegto darbuotojo tai neliečia.
4. **Kritikas** — puola užduotį, nustato kokybės kartelę, **pritaikytą tikslui ir luboms**: vienos eilutės tikslas su 1 € lubomis gauna kartelę, kurią geras laisvai samdomas specialistas pasiekia per popietę, surašytą keliais patikrinamais teiginiais — niekada „nulis faktinių klaidų istoriko akimis“, papildomi metodologijos skyriai ar formatai, kurių neprašei. `trivial` tikslams praleidžiamas.
5. **Planas** — dalys su `depends_on`. `trivial` tikslams praleidžiamas (vienas darbuotojo žingsnis tiesiai tikrintojui; planuotojas įsijungia tik jei tikrinimas nepavyksta). Nepriklausomos dalys gali vykti lygiagrečiai (iki 4).
6. **Darbuotojai** — kiekviena dalis yra atskiras pokalbis, bendras katalogas.
7. **Tikrintojas** — vertina **visą** rezultatą. Trivialioms užduotims naudoja galutinio failo ištrauką: ne daugiau kaip du modelio kvietimai, be įrankių ciklo. Vidutinėms užduotims skiriama iki 4 skaitymo ėjimų, sudėtingoms — iki 6; jei tiekėjas nederina įrankių su struktūrizuotu atsakymu, lieka atsarginis vien JSON kvietimas. Failus atsidaro pats (`read_file`, `list_dir`, `grep`, `office_read` skaidrėms ir lentelėms). Radinius skirsto į **blokuojančius** trūkumus (failo nėra, ne ta kalba ar formatas, trūksta privalomos dalies, esminis turinys klaidingas) ir **pastabas** (šlifavimas, antraštės, citatos, kurių niekas neprašė). Blokuojantis trūkumas = nesėkmė: planuotojas perskirsto, darbuotojai tęsia. Vien pastabos paleidimą baigia iš karto kaip `passed_with_notes` — gauni failą ir pastabas, o ne moki už šlifavimo ciklus. Perplanuojant **galutinio failo kelias išlieka**: žingsniai gali keistis, failas, kurį gausi, — ne.
   Jei dvi patikros iš eilės nepavyksta, Rocky laiko, kad problema — darbuotojo modelis: planą palieka, o **darbuotoją keičia** stipresniu — pajėgiausiu modeliu iki ~12 $/M iš dar nebandytos šeimos (`stall` įvykis), užuot planavęs iš naujo.
   Kiekvienas perplanavimas mato **jau bandytus planus ir jų įverčius** ir privalo keisti požiūrį, o ne pateikti tuos pačius žingsnius. Užbaigti žingsniai išsaugomi tik tada, kai jų nurodyti failai dar yra diske, todėl taisant nemokama antrą kartą už jau patikrintą tyrimą ar tekstą; ištrynus failą, žingsnis vykdomas iš naujo. Jei du ciklai iš eilės nepavyksta neaplenkę geriausio ligšiolinio įverčio, Rocky **sustoja anksčiau** (`no_progress` įvykis) ir likusias lubas palieka `rocky resume` su pakeista užduotimi, užuot mokėjęs už dar du tokius pačius ciklus.
8. Stabdo, kai pavyksta, baigiasi ciklai arba pasiekiamos eurų lubos. Trys pabaigos: **passed** — pavyko; **passed_with_notes** — ciklai ar lubos baigėsi, galutinis failas yra diske ir tikrintojas sako, kad jis **naudojamas** (žmogus galėtų jį panaudoti tikslui, pats per valandą pataisęs išvardytus punktus), arba įvertis ≥ 0,7; gauni failą ir pastabas, o likę blokuojantys trūkumai eina pirmi kaip `must fix: …` (`--json` jas grąžina lauke `notes`, išeities kodas 0); **unmet** — tikrintojas sako, kad rezultatą reikia daryti iš naujo, arba failo nėra. Ciklo viduje „beveik gerai“ vis tiek yra nesėkmė — būtent ji verčia perplanuoti.
   Paskutinės kiekvieno paleidimo eilutės — **perdavimas**: `result deliverables/…` (ir `also written …` užrašams bei kitiems failams) arba `no result … was never written`, `partial work …` su tuo, kas vis dėlto atsirado diske, ir `continue with rocky resume <id>`. Klaidos, paraginimai, nebaigti žingsniai, suspaudimai ir ciklo sargas spausdinami žodžiais, ne kaip neapdoroti įvykių žodynai. `--json` tą patį grąžina laukuose `deliverable_path`, `deliverable_exists` ir `files`; `meta.json` juos taip pat saugo.

Kiekvienas darbuotojo pokalbis ribojamas 20 ėjimų ir laikomas mažas: seni įrankių rezultatai virsta vienos eilutės nuorodomis, o viršijus ~60 % modelio lango **arba 100 tūkst. žetonų** (kas anksčiau) pokalbio vidurį apibendrina pigus įvertinimo modelis (`compact` įvykis). 100 tūkst. lubos svarbios darbuotojams su 1 mln. langu: be jų ilgas tyrimo žingsnis kiekvienu ėjimu iš naujo siųstų šimtus tūkstančių žetonų.

„Anthropic“ modeliams sistemos blokas ir užduoties žinutė pažymimi **prompt-cache** taškais, todėl kiekvienas kitas darbuotojo ar tikrintojo ėjimas juos perskaito kešo kaina (kvite — `cached`). OpenAI, Gemini ir DeepSeek stabilų pradžios fragmentą kešuoja patys; Rocky kiekvieno pokalbio sistemą + užduotį tarp ėjimų laiko baitas į baitą tokią pačią, kad kešas pataikytų.

Darbuotojas, kartojantis lygiai tą patį įrankio kvietimą, trečią kartą atmetamas, ketvirtą — stabdomas (`loop_guard` įvykis).

Prieš kiekvieną pokalbio kvietimą Rocky įvertina užklausos kainą ir apriboja `max_tokens` tiek, kiek dar leidžia lubos. Neįperkama užklausa sustabdoma dar nepasiekusi tiekėjo (`budget` įvykis, etapas `preflight`). Tikrosios sąnaudos vis tiek imamos iš tiekėjo kvito: įvertis gali skirtis, o paveikslėlių ar įrankių mokesčiai ir kainų pokyčiai gali jį viršyti. Todėl lubos yra saugiklis, o ne absoliuti sąskaitos garantija. Tikrinimui virš lubų papildomos atsargos neduodama.

Kiekvienas atsakymas spausdina **kvitą**: įėjimo / išėjimo / viso žetonai (plius samprotavimo ir kešuoti žetonai, kai tiekėjas juos praneša), USD, EUR (3 skaitmenys po kablelio). Galutinis kvitas dar **suskaidytas pagal vaidmenis** — `assess €0.001 · critic €0.021 · planner €0.034 · worker €0.012 · verifier €0.009` — kad matytum, kuris modelis valgo biudžetą. `--json` tą patį grąžina lauke `by_role`.

---

## 6. Atsijungti ir vėl prisijungti (kaip tmux)

Paleidimas yra **atskiras procesas**. Uždarius žiūryklę darbas nenutrūksta.

| Esi | Atsijungti (darbas tęsiasi) |
| --- | --- |
| CLI attach | **Enter** arba **Ctrl+C** |
| TUI | **q**, tada Enter (arba Ctrl+Q) |

Atsijungimas darbo niekada nenutraukia. Nutraukti: `rocky stop` (naujausią paleidimą) arba `rocky stop ID`. Darbas gauna akimirką švariai išeiti, paleidimas pažymimas `stopped`, kas įrašyta — lieka diske, o `rocky resume ID` vėliau jį pratęsia.

Sugrįžti:

```bash
rocky ps
rocky attach
rocky attach 20260827-14
rocky tui --attach 20260827-14
```

Žurnalai darbo kataloge:

```
.rocky/runs/<paleidimo-id>/
  meta.json
  events.jsonl
  worker.log
```

Jei Rocky klausia biudžeto ar patikslinimo, kol esi atsijungęs, prisijunk ir atsakyk. Darbuotojas laukia (apie 10 minučių), tada tęsia su saugia numatytąja reikšme.

### Pratęsti nulūžusį paleidimą

```bash
rocky resume            # naujausias unmet / error / crashed paleidimas šiame kataloge
rocky resume 20260828-11
rocky resume --budget 3 # naujos lubos tęsiniui
```

Viskas, ko reikia, jau guli `events.jsonl` ir darbo kataloge: įvertinimas, kritiko kartelė, planas, sudėtis, paskutinis verdiktas ir kurie žingsniai baigti. Pratęstas paleidimas **nemoka** už įvertinimą / kritiką / planą antrą kartą. Žingsniai, baigti po paskutinio plano ir kurių `writes` failai tebėra diske, praleidžiami (`subtask_skipped` įvykis); kiti vykdomi įprastai. Jei senąjį paleidimą tikrintojas jau buvo įvertinęs, pratęsimas pradedamas perplanavimu nuo to verdikto. Naujas paleidimas gauna savo ID, susiejamas su senuoju (`rocky ps` rodo `↻`) ir palieka senąsias lubas, nebent nurodai `--budget`.

Laikini „OpenRouter“ sutrikimai — laiko limitai, nutrūkęs ryšys, 408 / 429 / 5xx, tušti atsakymai — kartojami iki 3 kartų su pauze, kol paleidimas pasiduoda, tad vienas sutrikimas 25 minučių darbo nebenužudo.

### Tęsti nuo gero rezultato

```bash
rocky open                                   # atidaro naujausią galutinį failą
rocky resume "sutrumpink iki 300 žodžių"     # tęsia nuo naujausio baigto paleidimo
rocky resume 20260905-11 "pridėk CTA kiekvienam skelbimui"  # arba nuo to konkretaus
```

Pavykęs paleidimas niekada nepratęsiamas toks, koks yra (nėra ko perdaryti), bet su nurodymu jis tampa **tęsinio** pagrindu: įvertinimas, kritiko kartelė, sudėtis ir galutinio failo kelias išlieka, o planuotojas perplanuoja nuo tavo nurodymo taip, lyg jo būtų paprašęs tikrintojas. Naujas paleidimas susiejamas su senuoju kaip bet kuris pratęsimas. Interaktyvioje CLI tas pats nutinka tiesiog įrašius kitą nurodymą — atmintis jį susieja su paskutiniu galutiniu failu, — bet `resume` praleidžia įvertinimo / kritikos kvietimus ir prisega failą.

---

## 7. Gyva būsena

Terminale viena būsenos eilutė perrašoma toje pačioje vietoje (kaip Claude Code / OpenCode): `thinking · plan`, `researching · …`, `writing · article.md`. Planas, kritikas, tikrinimas ir kaina lieka kaip skydeliai. Enter atjungia.

---

## 8. CLI klavišai

- Įrašyk tikslą, Enter — paleidžia ir prisijungia.
- Tuščia eilutė arba `q` **tikslo** eilutėje — išeiti iš Rocky.
- **Enter** arba **Ctrl+C**, kol paleidimas rodomas — atsijungti (darbas tęsiasi; `rocky stop` jį nutraukia).
- **Ctrl+C** tikslo eilutėje — išeiti iš Rocky.
- Jei agentas klausia — įrašyk `yes` / `keep` / skaičių / trumpą atsakymą.

Kopijavimas CLI — įprastas terminalo žymėjimas: pažymėk, tada Cmd+C (macOS) arba Ctrl+C.

---

## 9. TUI klavišai

Kol paleidimas prijungtas, viena gyva eilutė rodo dabartinį veiksmą: `thinking · plan`, `researching · …`, `writing · article.md`. Ji lieka toje pačioje eilutėje virš trijų langų.

Langai: **planas** | **darbas** | **tikrinimas**. Rašyk laukelyje apačioje.

| Klavišas | Veiksmas |
| --- | --- |
| Enter | Pateikti naują tikslą arba atsakyti į klausimą |
| q, tada Enter | Atsijungti (paleidimo **nenužudo**; įvesties laukelis veikia ir kol paleidimas rodomas) |
| Ctrl+Q | Uždaryti TUI |
| Page Up / Page Down | Slinkti darbo žurnalą |
| Tempimas pele | Žymėti tekstą |
| Cmd+C arba Ctrl+C | Kopijuoti pažymėjimą (macOS naudoja tikrą iškarpinę) |
| Cmd+V arba Ctrl+V | Įklijuoti į įvesties laukelį |

Ctrl+C kopijuoja, o ne „nužudo programą“. Shift+tempimas naudoja paties terminalo žymėjimą, jei kada prireiktų.

---

## 10. Komandos tinklo sąsaja

```bash
rocky serve
rocky serve --host 0.0.0.0 --port 8787
```

Aplinkos kintamieji:

- `ROCKY_TOKEN` — bendras puslapio slaptažodis. **Nustatyk prieš klausydamas ne vien vietinio adreso.** Puslapis jį laiko slapuke ir `Authorization` antraštėje, niekada URL.
- `ROCKY_WORKSPACES` — kableliais atskirti leidžiami katalogai. Nenustačius leidžiamas tik katalogas, kuriame paleistas `rocky serve` (ir jo pakatalogiai).

Kai tik tikslas panašus į reklamos užduotį, po tikslo laukeliu parodoma, ką Rocky patikrins. **keisti** išjungia patikrinimą šiam katalogui (saugoma `.rocky/playbook.json`), ir jo radiniai nebepasirodo. Iš puslapio paleistas darbas — **tas pats foninis darbas** kaip `rocky "tikslas"`: jis matomas `rocky ps`, o `rocky attach`, `rocky stop`, `rocky resume` ir `rocky open` su juo veikia. Kiekvienas atidarytas skirtukas seka paleidimo `events.jsonl` savo žymekliu, tad antras žiūrovas ar puslapio perkrovimas mato visą paleidimą. Puslapis rodo biudžeto pasiūlymus, `clarify` klausimus (atsiranda atsakymo laukelis — darbas laukia apie 10 minučių, tada tęsia su saugiu numatytuoju atsakymu), strigtis, paraginimus, nebaigtus žingsnius, verdiktą „blokuojantys / pastabos“ ir galutinį perdavimą (rezultato kelias, dalinis darbas, pastabos). **Stop the run** nutraukia darbą kaip `rocky stop`. Sąrašas apačioje rodo katalogo paleidimus; spustelėk vieną, kad jį sektum.

---

## 11. Darbo katalogas ir saugumas

Rocky **visiškai autonomiškas kataloge `--dir`**. Rašant failus klausimo „ar tikrai?“ nėra. Failų įrankiai atmeta kelius už katalogo ribų.

`bash` turi dvi apsaugas:

1. **Draudžiamųjų sąrašas** — komandos, griaunančios nepriklausomai nuo katalogo, atmetamos dar nepaleistos: `/` ar `~` trynimas, `mkfs`, `dd` į įrenginį, „fork bomb“, `curl … | sh`, `sudo`, išjungimas, `git push --force`, `git reset --hard`, `git clean -fdx`, `chmod 777 /`, taip pat virtualios aplinkos kūrimas ar keitimas (`uv venv`, `uv run --python`, `python -m venv` — darbuotojas kartą perrašė projekto `.venv` tuščiu interpretatoriumi; Rocky `python3` jau turi „Office“ bibliotekas ir darbuotojo `PATH` eina pirmas). Savų taisyklių pridėk per `ROCKY_BASH_DENY="npm\s+publish;terraform\s+apply"`.
2. **Smėlio dėžė** (įjungiama atskirai) — `ROCKY_SANDBOX=sandbox-exec` (macOS) leidžia komandai skaityti viską, o rašyti tik `--dir` ir laikinuose kataloguose. `ROCKY_SANDBOX=docker` vykdo konteineryje, darbo katalogas prijungtas kaip `/work` (`ROCKY_SANDBOX_IMAGE`, numatytai `python:3.12-slim`; jei darbuotojai kuria „Office“ failus, į savo atvaizdą įdiek `python-pptx`, `openpyxl` ir pan.). Numatytoji reikšmė `none`: procesas startuoja `--dir`, bet komanda vis tiek gali `cd`, `curl` ar liesti failus kitur.

Mac'e nustatyk `ROCKY_SANDBOX=sandbox-exec`. Tai nieko nekainuoja ir yra skirtumas tarp „agentas redagavo mano projektą“ ir „agentas redagavo mano namų katalogą“.

Nenukreipk `-d` į namų katalogą. Naudok atskirą projekto katalogą.

Niekada neįrašyk `OPENROUTER_API_KEY` į git. Laikyk `~/.env` arba projekto `.env`, kurio git nemato.

`rocky serve` numatytai klausosi `127.0.0.1` (tik ši mašina). `rocky serve --host 0.0.0.0` **be** `ROCKY_TOKEN` atsisako paleisti: procesas baigiasi, serveris neprisijungia. Prieš atverdamas sąsają nustatyk `ROCKY_TOKEN`.

---

## 12. Darbo katalogo tvarka

Kiekvienas planas laikosi vienos konvencijos — su receptu ar be jo:

| Kas | Kur |
| --- | --- |
| Darbiniai failai — tyrimai, užrašai, juodraščiai, skriptai, tarpiniai duomenys | `runs/notes/` |
| Galutiniai failai — tai, ko prašei | `deliverables/` |
| Katalogo šaknis | nieko, nebent tiksle nurodai kelią šaknyje |

Planuotojas ir kiekvienas darbuotojas šią taisyklę gauna prompt'e; plikas galutinio failo pavadinimas iš įvertinimo ar planuotojo (`summary.md`) automatiškai dedamas į `deliverables/`. Įmontuotas rinkodaros receptas rašo `runs/notes/research.md`, `runs/notes/voc.md`, `runs/notes/copy.md`, tada `deliverables/campaign.pptx` ir `deliverables/variants.xlsx`.

---

## 13. Įrankiai, kuriuos gali kviesti darbuotojas

`bash`, `read_file`, `write_file`, `append_file`, `patch`, `list_dir`, `grep`, `todo`, `web_search`, `web_fetch`, `browser_navigate` / `browser_snapshot` / `browser_close`, `vision_analyze`, `image_generate`, `clarify`, `skills_list`, `skill_view`, `office_read`, `memory`, `brief`, `runs_search`, `recipe`, `ad_board`, `unit_economics`, `customer_economics`, `experiment_plan`, `funnel_analyze`, `table_analyze`, `repeat_buying`, `reach_overlap`, `media_plan`, `campaign_calc`, `page_inspect`, `seo_inspect`, `web_check`, `claim_check`, `survey_check`, `outcome_scores`, `research_plan`, `plan_check`, plius bet kurie tavo sukonfigūruoti **MCP** įrankiai (žemiau).

### Užduoties aprašas

`brief` saugo vieną struktūruotą užduoties aprašą kiekvienam darbo katalogui faile `.rocky/brief.json`: prekės ženklą, URL, produktą, auditoriją, pasiūlymą, kainą ir savikainą, apimtį, kanalus, KPI, biudžetą, kalbą, apribojimus, rinką, konkurentus ir terminą. Kiekvienas vaidmuo jį mato kaip BRIEF bloką savo prompt'e, todėl darbuotojas pirmiausia jį perskaito ir klausia tavęs tik to, ką jo darbo rūšiai (reklamos, kainodara, testas, medija, tyrimas) išvardija `missing_for`. Prie kiekvieno lauko įrašoma, kas jį nustatė ir kokiu įrodymu; darbuotojo be URL ar failo spėta reikšmė žymima kaip *prielaida*, kol jos nepatvirtinsi arba nebus pridėtas įrodymas. `unit_economics`, `experiment_plan`, `reach_overlap`, `ad_board` ir `page_inspect` trūkstamus argumentus užpildo iš aprašo. Kai rezultatas rekomenduoja išlaidas, darbuotojas įrašo sprendimą – vieną vidinę stiprybę ar silpnybę, vieną išorinę galimybę ar grėsmę, investiciją ir tai, kas priverstų persigalvoti, – o aprašas pažymi kiekvieną trūkstamą ar bendro pobūdžio dalį. `clear` ištrina laukus arba visą aprašą.

### Skaičiai

`unit_economics` atlieka kainodaros, nuolaidų, akcijų ir pritraukimo sprendimų aritmetiką, kad darbuotojas niekada jos nespėliotų tekste: vieneto įnašą (contribution) ir maržą, bendrą įnašą, lūžio taško vienetus, kai nurodytos `fixed_costs`, o nurodžius `new_price` arba `discount_pct` — kiek pardavimų reikia, kad išliktų šiandieninis įnašas (`required_units`, `extra_units`, `required_uplift_pct`). Pridėjus `expected_uplift_pct`, grąžinamas sprendimas ir vieneto savikaina bei augimas, ties kuriais sprendimas apsiverčia. Kai `variable_cost` nežinoma, rezultate tai nurodoma lauke `missing` ir pateikiama sąlyginė lentelė pagal tipines maržas; tada darbuotojas paklausia tavęs per `clarify` arba aiškiai įvardija prielaidą.

`unit_economics` dabar atsako ir į mažmeninės prekybos, pardavimo kanalų ir akcijų klausimus, kurie anksčiau buvo sprendžiami tekste. `capacity` įvertina laiko juostomis neparduotas kirpyklos, studijos ar salės vietas ir pasako, kokia dalis ne piko rezervacijų gali būti persikėlę nuolatiniai klientai, kol nuolaida dar neneša nuostolio; `ownership` prie bazinės kainos prideda eksploatacines medžiagas per pasirinktą laikotarpį ir randa momentą, nuo kurio brangesnis aparatas su pigesnėmis kapsulėmis tampa pigesniu pirkiniu; `offers` palygina išbandymą su mokėjimu vėliau ir pinigų grąžinimo garantiją pagal grąžinimus, neapmokėtas sąskaitas ir išieškojimo kaštus, įkainoja mokamą parinktį, pavyzdžiui, pristatymą tą pačią dieną, pagal konversijos prieaugį, kurį ji turi atnešti, ir patikrina rekomendacijų atlygį pagal įnašą, į kurį jis turi tilpti. `line` per pasirinktą laikotarpį sveria naują variantą ar premium liniją prieš viso prekės ženklo perkainojimą – su kūrimo kaštais, perėjimu nuo bazinio produkto, pereinamaisiais mėnesiais, atsipirkimu ir tašku, kuriame verdiktas apsiverčia, – ir išskaido kainų istoriją pagal laikotarpius į kainos, apimties ir savikainos efektus. `retail` apima paletės skaidymą į mažesnes partijas, mažos maržos liniją, vertinamą pagal krepšelius, kuriuos ji pritraukia, tai, kas kanale prisiima nukainojimus ir neparduotas prekes, funkcijas, kurias prekės ženklas perima parduodamas tiesiogiai, lentynos kainos „krioklį“ iki to, kas lieka paklausos kūrimui, išstūmimą iš lentynos, kelius su nuostoliais kelyje, kupono panaudojimus ir atsargų trūkumą, pristatymo kainas pagal regionus, mokėjimo sąlygas, GMROI, grąžinimų rezervą ir pardavėjų skatinimo schemas; `media_value` įkainoja mokamą raktažodžio testą, didesnį reklamos formatą, katalogo tiražą, du biudžeto lygius pagal absoliutų pelną, o ne ROAS, viešojo intereso kampanijos rezultatą, partnerystės grynuosius pinigus, nepiniginį indėlį ir rezultatus kaip tris atskirus skaičius ir laukiamą grąžą prieš to paties konteksto palyginamuosius rodiklius; `routes` įvertina sistemos perkūrimą prieš patobulinimą pagal segmentui svarbiausius rezultatus. Kiekvienas blokas lauke `missing` įvardija, ko trūksta, vietoj spėjimo pateikia sąlyginę lentelę ir prieštaringus duomenis atmeta paprastais žodžiais. `segments` kategorijos nevartotojus ir prekių ženklo keitėjus įkainoja atskirai: leistiną pritraukimo kainą kiekvienam segmentui iš įnašo ir laukiamų pirkimų, šiandieninės CPA santykį su ja ir verdiktą pritraukti / riba / stabdyti bei biudžeto padalijimą pagal atsargą, kad bendra CPA niekada nepaslėptų nuostolingo segmento.

Paklausk Rocky: „Parduodame 100 vnt. po 100 €, kintamoji savikaina 60 €. Jei kainą sumažinsime iki 90 €, kiek papildomų pardavimų reikės?“ — atsakymas: **34 papildomi pardavimai, iš viso 134**, kad išliktų 4 000 € įnašo.

Dabar jis vienu kvietimu aprėpia visą kainodaros užduotį. Šalia maržos pateikia antkainį (o vietoj vieneto savikainos priima `margin_pct` arba `markup_pct`), parodo po nukainojimų ar pardavimų struktūros realiai gautą kainą ir surikiuoja kelis kainų planus pagal tikėtiną grynąjį įnašą, nurodydamas, kiek pardavimų kiekvienas turi atsikovoti. Taip pat įvertina rinkinį, palyginti su atskirai perkamomis prekėmis, ir nurodo, kokia rinkinio pasirinkimo dalis atperka nuolaidą; sumaišo pakopas ar variantus su baziniu pasiūlymu ir pasako, kokia visą kainą mokančių pirkėjų dalis gali pereiti į pigesnę pakopą, kol bendras įnašas nenukris; suskaičiuoja kainos susidarymą per platinimo grandinę iki lentynos kainos ir atgaline tvarka randa gamybos savikainą, kuri pasiektų tikslinę kainą; o prie lūžio taško ir atsipirkimo prideda vienkartinius, periodinius ir vienetinius įsipareigojimus. Nulinė kaina priimama nemokamai pakopai, kai ekonomiką lemia įsipareigojimai arba mokami variantai.

`customer_economics` atlieka kliento pritraukimo ir išlaikymo aritmetiką, kad darbuotojas niekada nerašytų „CLV yra maždaug 3× CAC“ iš atminties: įnašą per laikotarpį iš `price` + `variable_cost` (arba `arpu` + `gross_margin_pct`), tikėtiną kliento trukmę iš nubyrėjimo, CLV per horizontą (numatytai 36 mėnesiai arba 3 metai, pasirinktinai diskontuotą), atsipirkimo laikotarpį ir verdiktą `acquire` / `marginal` / `do not acquire at this CAC` pagal `target_clv_cac_ratio` kartu su didžiausia leistina CAC ir didžiausiu nubyrėjimu, prie kurio pritraukimas dar atsiperka. Papildomi blokai įsijungia tik tada, kai pateikti jų duomenys: `returns`, `category_ceiling`, `funding` (kainos mažinimas kaip pritraukimo išlaidos), `follow_on` (nemokamas ar subsidijuotas įėjimo pasiūlymas prieš mokamus tęstinius pirkinius, su lūžio prisijungimo rodikliu), `subscription_rollout`, `channels` (CPA, AOV, grynasis rezultatas, reitingas ir žyma „pelningas, bet antraeilis“) ir `research_value`. Ko trūksta, įvardijama lauke `missing`, o kai nežinoma savikaina arba išlaikymas, pateikiama sąlyginė lentelė, o ne spėjimas.

`experiment_plan` apskaičiuoja A/B testo dydį, užuot leidęs darbuotojui spėti „paleiskime dviem savaitėms“: iš `baseline_rate` (procentais), `minimum_effect` — mažiausio verto aptikti pokyčio (numatytai santykinio, arba absoliutaus procentiniais punktais) ir `weekly_traffic` grąžina imtį vienam variantui, bendrą imtį, pilnų savaičių skaičių ir sprendimo taisyklę (iš anksto fiksuota imtis, jokio ankstyvo stabdymo, koks pokytis ir p reikšmė laikomi laimėjimu). Keli variantai prieš kontrolę dalijasi alfa. Nurodžius `max_weeks`, pasako, kokį pokytį įmanoma aptikti per turimą laiką. Be srauto duomenų trukmė pateikiama kaip nežinoma, ir darbuotojas jos paklausia.

Rocky dabar gali ne tik suplanuoti testą, bet ir perskaityti jo rezultatą. Nurodyk `experiment_plan` įrankiui `mode: evaluate` ir variantus su parodymų ir konversijų skaičiais (arba vidurkiu ir standartiniu nuokrypiu, kai matuojama reikšmė), ir kiekvienam variantui gausi skirtumą, jo 95 % pasikliautinąjį intervalą, p reikšmę ir verdiktą: laimėtojas, pralaimėtojas, skirtumo neaptikta arba per mažai duomenų – tęsk testą, kai suplanuota imtis dar nepasiekta. Pateikus prieš testą surašytą planą (imtį vienam variantui ir mažiausią reikšmingą pokytį), verdiktas laikosi šios taisyklės, todėl statistiškai reikšmingas, bet už suplanuotą mažesnis pokytis įvardijamas, o ne diegiamas. Apsauginiai rodikliai, pavyzdžiui, grąžinimų dalis ar užsakymo vertė, tikrinami iš tų pačių skaičių, o jų pažeidimas nurodomas prie laimėtojo. Matuojamoms reikšmėms `mode: plan_means` apskaičiuoja imtį pagal standartinį nuokrypį (arba pagal 20 vienetų bandomąjį matavimą, kai jis nežinomas), o porinis dizainas, kai kiekvienas dalyvis išbando visus variantus, reikalauja mažiau žmonių.

Tas pats įrankis skaito tyrimų duomenis ir sudėlioja tyrimus jų nevykdydamas. `mode: wtp` Van Westendorpo atsakymus paverčia keturiais kainos taškais ir priimtinu intervalu, įspėja, kai respondentų mažiau nei 30, ir suplanuoja „netikrų durų“ kainos puslapį su imtimi kiekvienai kainai ir privalomu atskleidimo sakiniu. `mode: concept` perskaito atliktą koncepcijos testą: pirmiausia supratimą, tik tada ketinimą, todėl niekieno nesuprasta koncepcija grąžinama aiškesnei žinutei, o ne vertinama pagal ketinimą pirkti. `mode: test_market` patikrina, ar pilotas turi pilną rinkodaros kompleksą, ar rinkos pajamos ir dalis nenutolusios nuo šalies vidurkio, ar KPI ir perskaitymo datos nustatytos iš anksto. `conjoint_design` ir `conjoint_analyze` sudaro subalansuotas pasirinkimo užduotis ir pateikia skaičiavimu pagrįstas dalines naudas, taip ir pažymėtas; `screener` surašo pirkimo lango klausimą lietuviškai ar angliškai ir iš dažnio bei atsako rodiklio apskaičiuoja, kiek kvietimų reikia; `choice_study` suprojektuoja antrojo pasirinkimo testą su vienodai aprašytomis kortelėmis ir išgalvoto prekių ženklo atskleidimu, o paskui perskaito perėjimo dalį su intervalu. `mode: creative_test` pagal `design` suplanuoja ar perskaito aštuonis dizainus: holdout (pokytis skaičiuojamas tik tada, kai kontrolinė grupė buvo atsitiktinai atskirta iš tos pačios populiacijos), qualified_readout, copy_test su iš anksto fiksuota stabdymo taisykle, diff_in_diff su įvardyta lygiagrečių trendų prielaida, crossover, portfolio (pradinio ir rezervinio biudžeto padalijimas su perskirstymo taisykle), factorial (langeliai ir savaitės) ir stated_vs_observed, kur prognozė remiasi stebėtu, o ne apklausoje deklaruotu rodikliu.

`reach_overlap` neleidžia darbuotojui sudėti kanalų pasiekiamumo taip, lyg niekas nematytų dviejų kanalų: iš `audience`, kiekvieno kanalo `reach` (žmonėmis arba su `reach_unit: pct`) ir žinomų porinių `overlaps` grąžina bendrą pasiekiamumą kaip apatinę ir viršutinę ribą, taškinį įvertį tik pagal įvardytą modelį (`assumption: independent` – atsitiktinis dubliavimasis, arba tavo pateiktos sankirtos) ir kiekvieno kanalo prieauginį pasiekiamumą godžia tvarka pagal pasiekiamumą už išleistą eurą. Nežinomos poros išvardijamos lauke `missing`, `bounds_width_pct` parodo, koks platus neapibrėžtumas, o `narrows_most` įvardija vienintelę sankirtą, kurios verta paklausti, tad darbuotojas paklausia tavęs per `clarify` arba aiškiai įvardija prielaidą, užuot išsigalvojęs skaičių. Nurodžius kanalų `cost`, pridedama kaina už tūkstantį pasiektų žmonių ir `budget` patikra; nurodžius `frequency` – parodymai, GRP ir vidutinio dažnio intervalas.

`table_analyze` perskaito darbo kataloge esantį CSV arba XLSX failą kaip duomenis ir atsako į vieną klausimą apie jį tikrais skaičiais, kad nė vienas darbuotojas neapibendrintų lentelės „iš akies“: `describe` išvardija stulpelius, jų tipus, tuščius langelius ir reikšmių ribas; `group` skaičiuoja, sumuoja arba vidurkina rodiklį pagal vieną ar kelis stulpelius (arba pagal datos stulpelio `week:` / `month:`) ir įvardija skirtumą tarp geriausios ir blogiausios grupės; `crosstab` pateikia langelių skaičius, eilučių dalis ir chi kvadrato testą, kai kiekvieno langelio laukiamas dažnis yra bent 5; `before_after` palygina vidurkius prieš ir po `split_date` Welcho t testu, prireikus normalizuodamas pagal `normalize_by`; `period_compare` parodo paskutinį laikotarpį prieš ankstesnį ir prieš tą patį laikotarpį prieš metus; `proportion_diff` palygina dviejų grupių dalis su Waldo 95 % intervalu ir z testu (arba pateikia vieną dalį su Wilsono intervalu); `intervals` iš dviejų datų stulpelių apskaičiuoja p25/p50/p75/p90/maks. dienas; `coverage` sugretina sąrašo dalis su `population_shares`; `text_themes` suskaičiuoja temas pagal reguliariąsias išraiškas laisvo teksto stulpelyje; `search_terms_split` padalija „Google Ads“ paieškos terminų eksportą į prekės ženklo ir ne prekės ženklo išlaidas, CPA ir ROAS. Mažesnės nei `min_rows` (numatytai 30) grupės ar langeliai pažymimi kaip per maži ir niekada nereitinguojami; rezultate nurodomos iš tikrųjų aprėptos datos, panaudotos eilutės, o skiltyje `missing` – tušti langeliai, netinkami skaičiai, neįskaitomos datos ir praleistos sumų eilutės. Kai skaičiuojama statistika, `method` pasako, kaip ji gauta.

Dar septyniolika `table_analyze` operacijų skaito failus, kuriuos rinkodarininkas jau turi. `cohort_compare` sugretina paveiktą kohortą (id sąrašą, filtrą arba pirmosios datos intervalą) su visais kitais – grįžusių ir sėkmingų dalis, skirtumą procentiniais punktais su Waldo intervalu, o kohortai, mažesnei už `min_cohort`, procentų nepateikia; nurodžius `period`, sudaro pirmojo įvykio kohortas, kurių išlaikymas kiekviename poslinkyje cenzūruojamas, o ne skaičiuojamas kaip nubyrėjimas, arba pagal `compare_column` padalija eilutes ir palygina medianas bei užbaigimą; `change_impact` seką apie `change_date` skaito kaip langus „prieš / anksti / vėliau“ su skundais tūkstančiui užsakymų, ±2 standartinių nuokrypių triukšmo juosta, lūžio tašku iš slenkančio vidurkio ir pagal segmentus pasako, ar visos paieškos sistemos krito tą pačią dieną; `media_response` pateikia išlaidų ir pardavimų koreliaciją su 0–4 savaičių vėlavimu (pažymėta kaip koreliacija, o ne priežastis) ir poveikio gesimą po kampanijos su pusėjimo trukme. `price_response` apskaičiuoja lanko ir kryžminį elastingumą bei įnašo pokytį visoje linijoje (taip pat iš tiesiogiai pateiktų skaičių „prieš“ ir „po“), `promo_effect` – bazinį, akcijos ir poakcinį vidurkį kiekvienai pakuotei su portfelio grynuoju rezultatu, `choice_mix` – kurias konfigūratoriaus parinktis pirkėjai renkasi patys, o `basket` – kartu perkamų prekių dalį, pakilimo koeficientą ir pakartotinį pirkimą pasirinktoms prekėms. `buyer_frequency` kiekvieną dažnio skaičių pateikia su pažymėtais išskirčių pirkėjais ir be jų, NBD juostas ir nuolat bei su pertraukomis pildžiusių namų ūkių palyginimą, o `panel` prieš bet kokią Dirichlet normą patikrina dalių stabilumą ir dubliavimosi dėsnį ir be `total_households` penetracijos neskaičiuoja. `survey`, `brand_recall` (spontaninis ir atpažintasis žinomumas visada kaip du skaičiai, klaidingas priskyrimas pagal konkurentus), `keyword_demand` (ilgoji uodega prieš pagrindinę frazę su nurodytomis CTR ir konversijos prielaidomis), `page_breadth` (skirtingi srauto sulaukę puslapiai per laikotarpį ir suindeksuoti, bet srauto negaunantys URL), `market_match` (testo ir kontrolės rinkų poros pagal koreliaciją, lygį ir aptinkamą prieaugį), `campaign_compare` (augimas vienam ESOV punktui su palyginamumo žymomis ir etalonu pagal prekės ženklo tipą), `session_log` ir `customer_audit` (dublikatai pagal sunormintą telefoną ar el. paštą, pasenusių įrašų dalis ir galimų siųsti kontaktų ribos) laikosi tų pačių taisyklių: per mažos grupės nereitinguojamos, spragos nurodomos skiltyje `missing`, pridedama `method` eilutė ir cituotina `summary`.

`repeat_buying` paverčia paties rinkodarininko operacijų failą (CSV arba XLSX, viena eilutė vienam užsakymui su pirkėjo ID, data ir, jei yra, suma, užsakymo numeriu, SKU, prekių ženklu ir vienetais) į lojalumo ir prekių ženklo augimo skaičius, kad teiginiai apie pakartotinius pirkėjus, penetraciją ar retai perkančius remtųsi skaičiavimu, o ne įspūdžiu. Pasirenkama `analysis`: `summary`, `frequency` (kiek pirkėjų pirko vieną kartą ir kokią pardavimų dalį jie atnešė), `repeat` (du pakartotinio pirkimo apibrėžimai greta, dienos tarp pirkimų ir pakartotinio pirkimo rodiklis per 30, 90 ir rekomenduojamą dienų skaičių), `skip_return`, `cohorts` (su pasirenkamu bazinio ir bandomojo laikotarpio palyginimu, skirstančiu pirkėjus į naujus ir grįžusius), `entrants_leavers` (nauji, praradę ir grįžę pirkėjai kiekvienu laikotarpiu, sukaupta penetracija, kai nurodytas `universe`, ir kiek laikotarpių sumavimas skaičiuoja dvigubai), `duplication` (kurie prekių ženklai dalijasi pirkėjais ir dubliavimosi koeficientas, repertuaro dydis, `focus_brand` poreikių dalis) ir `spikes` (laikotarpiai, viršijantys slenkančios medianos bazę, bazės augimas, lygis prieš ir po `change_date`). Dvi analizės failo nereikalauja: `double_jeopardy` pritaiko lojalumo priklausomybę nuo penetracijos logaritmo tarp prekių ženklų ir pasako, ar tiriamas ženklas yra virš linijos, žemiau jos ar atitinka savo dydį, o `share_vs_growth` skaito ženklo pardavimų augimą greta kategorijos ir pasako, ar rinkos dalis padidėjo, sumažėjo ar išliko. Kiekvienas atsakymas turi langą, panaudotas eilutes, kas buvo tuščia ar neperskaitoma, įspėjimus, pavyzdžiui, `small_base` (mažiau nei `min_buyers`, numatytasis 30), metodą ir vieną sakinį, kurį galima cituoti.

### Reklama prieš jos nusileidimo puslapį

`page_inspect` patikrina, ar puslapis, į kurį veda reklama ar el. laiškas, laikosi to, ką reklama žada, – kodu, o ne darbuotojo įspūdžiu. Nurodyk reklamą (`headline`, `primary_text`, `cta`, `price`, `audience`, `offer`, `language`) ir puslapį kaip `url`, darbo katalogo `path` arba patį `html`; įrankis ištraukia pavadinimą, H1 antraštes, pirmojo ekrano tekstą, mygtukus, kainas, formas ir kalbą. Tada grąžina išvadas su tikslia reklamos kopija ir tiksliu puslapio elementu prie kiekvienos: reklamoje nurodyta kaina, kurios puslapis nerodo, yra **blokas**, puslapis be jokio mygtuko ar formos – blokas, o H1 nepakartotas pažadas, raginimas, kurio nekartoja nė vienas mygtukas, puslapyje trūkstami auditorijos ar pasiūlymo žodžiai, pasikeitusi kalba ar per plonas puslapis – įspėjimai. Kainos sunormuojamos, todėl `1 200,50 €` ir `€1,200.50` sutampa, o lyginant žodžius toleruojamos lietuviškos galūnės. Reklamų receptas paleidžia jį kiekvienai reklamai prieš perduodant darbą; puslapis, kurio Rocky nepavyko parsisiųsti, patenka į `missing`, o ne į spėjimą.

Tuo pačiu kvietimu galima paleisti įvardytas patikras `checks`: kas valdo puslapį ir ar absoliutūs teiginiai turi šaltinį (`credibility`), ar veikia vidinės nuorodos (`links`, per variklio nuorodų tikrintuvą), kokius spaudimo elementus jis naudoja – laikmačius, likučių skaičius, iš anksto pažymėtus priedus (`trust`), ar kiekvienas kainų laiptelis duoda ką nors naujo (`price_ladder`), kuriuos faktus paieškos robotas mato neapdorotame HTML, o kurie atsiranda tik po atvaizdavimo (`required_facts` + `rendered_text`), kurie sekimo skriptai įkeliami prieš sutikimo juostą (`data_use`), schemą prieš matomą puslapį, navigaciją, pažadus, kontaktų taškus ir UTM žymėtas nuorodas. Nurodžius `query` ir `intent`, patikrinama, ar puslapis žodis po žodžio atsako į paieškos užklausą. Su `kind: receipt|shipping|welcome` tikrinamas transakcinis laiškas: prekės, suma, užsakymo numeris, pristatymas, pagalbos kontaktas ir atsisakymo nuoroda. Viskas, kam reikia naršyklės (ekrano dydis, paspaudimai, skriptai), lieka už jo ribų.

### Paieška

`seo_inspect` atlieka paieškos kanalo patikras, kurių rinkodarininkas nori prieš mokėdamas už srautą, – kodu, o ne akimis. Nurodyk `mode`: `snippet` perskaito puslapių pavadinimus ir aprašymus (ilgį, dublikatus, prekės ženklą, ar fragmento pažadas ir kaina matomi H1 ir pirmame ekrane, konkuruojančius pavadinimus pagal užklausą); `urls` įvertina, ar adresai skaitomi ir ar kanoninės žymos, noindex bei peradresavimai rodo viena kryptimi; `crawl_rules` išanalizuoja robots.txt pagal robotą, pasako, kurie svarbūs puslapiai užblokuoti ir kurių nėra svetainės žemėlapiuose (indeksai sekami trimis lygiais); `structured_data` palygina JSON-LD kainą, prieinamumą ir įvertinimą su tuo, ką rodo puslapis; `crawler_content` išvardija faktus, kurie atsiranda tik įvykdžius „JavaScript“; `templates` išmatuoja, kiek šabloninio puslapio teksto yra unikalu ir kurių pirkimo sprendimui reikalingų laukų trūksta; `serp` nustato savo ir konkurentų domenų pozicijas užklausų kopėčiose ir pažymi užklausas su mišria intencija; `keywords` suskirsto paieškos terminų eksportą į grupes, randa skelbimų grupes, kuriose maišomi skirtingi poreikiai, ir pateikia neigiamų raktažodžių kandidatus su suma, kurią jie sutaupytų. Puslapiai pateikiami kaip HTML, darbo katalogo kelias arba URL, kurį variklis parsiunčia; ko nepavyko parsiųsti, atsiduria `missing` – niekada nespėjama. Ištaisyk block lygio radinius, prieš pirkdamas srautą į puslapį.

### Klientų tyrimų dizainas

`survey_check`, `outcome_scores` ir `research_plan` kodu patikrina klientų tyrimų dizainą dar prieš užduodant pirmą klausimą. `survey_check` audituoja JSON formatu surašytą klausimyną: pažymi dviprasmius („ir / arba“) ir vedančius klausimus, miglotus dažnio žodžius be skaičiaus, uždarus sąrašus be „kita / nežinau“, nesuderintas skales, žargoną, „kodėl“ klausimus be juos pagrindžiančio elgsenos klausimo, praeities įvykius be laiko rėmo, vieną pasirinkimą ten, kur tikėtini keli atsakymai, maršrutizavimą (`ask_if`) į neegzistuojantį klausimą ar variantą, žinomumo apklausas, kurios atskirai nematuoja spontaninio prisiminimo, pažinimo su pagalba, reklamos prisiminimo ir pirmojo kontakto šaltinio, taip pat aklo palyginimo protokolus su fiksuota tvarka ar be aklos grupės; grąžina radinius pagal lygį, respondentų kelius su klausimų skaičiumi, numatomą trukmę ir penkių žmonių pilotinio bandymo planą. `outcome_scores` tikrina rezultato teiginius (kryptis + matas + objektas + situacija, be „geresnis / greitas / patikimas“, be funkcijos vietoje rezultato), skaičiuoja galimybės balą = svarba + max(svarba − pasitenkinimas, 0), kiekvieną rezultatą priskiria neaptarnaujamam, tinkamai aptarnaujamam ar per daug aptarnaujamam, nurodo atotrūkį nuo geriausio konkurento su `comparable` žyma tik tada, kai abiejų prekių ženklų n ≥ 30, o `job_map` režimu patikrina darbo žemėlapį pagal aštuonias fazes. `research_plan` koncepcijas ar fokus grupės kriterijus paverčia vykdomu dizainu: kvota kiekvienai koncepcijai, imties pakankamumas per `experiment_plan`, instrumentas, kuris praeina `survey_check`, sprendimo taisyklė su pažymėtomis numatytosiomis reikšmėmis ir įspėjimas `single_voice_criterion` dėl visko, ką pasakė vienas žmogus. Visi trys įrankiai pasako, ko trūksta, užuot išgalvoję skaičių ar klausimą.

### Planai, patikrinti prieš perduodant

`plan_check` kodu patikrina struktūruotą rinkodaros planą dar prieš jam pasiekiant rinkodarininką; `kind` parenka tikrintuvą. `content_calendar` randa savaites be įrašų, įrašus be atsakingo, datos ar tikslo, du įrašus tame pačiame kanale tą pačią dieną, planą, įstrigusį viename pirkimo etape, formatus, kurių nepagrindžia auditorijos įrodymai, laikinus įrašus be galiojimo pabaigos, perkrautą starto savaitę, persidengimą su pasenusiu esamu turiniu ir savaites, viršijančias nurodytą pajėgumą. `pr_board` tikrina pranešimą spaudai, pranešimą apie pokytį, rėmimo paketą ar žinučių namą: naujienos kabliuką, citatą su vardu ir pareigomis, ne ilgesnį kaip 100 žodžių aprašą apie įmonę, kontaktą ir datą, autoriaus eilutę su atskleista priklausomybe, šaltinį kiekvienam skaičiui (`claim_check`, kai nurodyti šaltiniai), partnerius su pagrįstais jų pačių tikslais, paveiktas grupes jų pačių žodžiais, skaitytojų priežastį kiekvienam leidiniui ir vieną pagrindinį sakinį su ne daugiau kaip trimis įrodytais ramsčiais, kurį išlaiko kiekvieno kanalo variantas. `demo_board`, `response_board`, `partner_board`, `qualification` ir `community_rules` tą patį daro demonstracijai, eskalavimo matricai, partnerių programai, pardavimų kvalifikavimo lapui ar atrankos klausimynui ir taisyklių dokumentui. `evidence_log` prideda pagrįstus pastebėjimus į `runs/notes/evidence.jsonl` ir patikrina antrinio šaltinio matą, populiaciją ir laikotarpį; `retell` ir `ad_pretest` teiginį ar skelbimą nusiunčia šaltam skaitytojui – modeliui, nemačiusiam užduoties, – ir įvertina, kurie `must_survive` terminai išliko, o be skaitytojo modelio grąžina užklausas, užuot vertinę patys. Kiekvienas režimas pasako, ko trūksta, užuot užpildęs langelį, kurį darbuotojas paliko tuščią. `content_calendar` patikra kartu įrašo `deliverables/calendar.html` – mėnesio tinklelį, kuriame kiekvienas įrašas yra kortelė, nuspalvinta pagal blogiausią radinį, bei sąrašą su radinių žymomis, tuščiomis savaitėmis, starto langu ir peržiūrai pribrendusiais amžinaisiais įrašais, – ir `calendar.csv` dvynį rinkodarininko skaičiuoklei; santraukoje įvardijamas puslapis, kad perdavimas rodytų į jį.

### Interneto patikros

`web_check` atlieka interneto patikras, kurias rinkodarininkas kitaip darytų akimis, – po vieną `mode` kiekvienam klausimui. `name_check` užrašo kandidatinį pavadinimą taip, kaip jį išgirdęs surinktų lietuvis ar anglas, kiekvieną rašybos variantą paieško ir praneša apie susidūrimus su kitu prekių ženklu ar žodyno žodžiu; `mention_scan` paieškos rezultatus apie dažną pavadinimą suskirsto į mūsų, dviprasmiškus ir kitus ir įvardija tą patikslinamąjį žodį, kuris juos geriausiai atskiria. `policy_finder` nuo pradinio puslapio seka poraštės, antraštės, pagalbos ir DUK nuorodas, kol randa pristatymo, grąžinimo ar vėlavimų puslapį, ir pacituoja pažado laukus, o `listing_check` katalogų įrašuose palygina pavadinimą, adresą, telefoną, darbo laiką ir svetainę su teisingais duomenimis. `search_intent`, `site_crawl`, `keyword_inspect` ir `link_prospects` apima paieškos pusę: ketinimą pagal modifikatorius, svetainės apėjimą, kuris išvardija nepasiekiamus svarbius puslapius, kas rikiuojasi pagal raktažodį ir ką jų puslapiai turi palyginti su jūsiškiu, ir kurie kandidatai tikrai nurodo į tikslinį puslapį. `locale_check`, `kit_check`, `email_auth` ir `search_region` užbaigia rinkinį. Ko įrankis negalėjo parsisiųsti, paieškoti ar patikrinti (DNS įrašų, regioninės paieškos, domeno laisvumo), jis išvardija skiltyje `missing` ir niekada nespėja.

### Teiginiai prieš šaltinius

`claim_check` kiekvieną išmatuojamą juodraščio teiginį sugretina su jį pagrindžiančia šaltinio ištrauka, kad nei darbuotojui, nei tikrintojui nereikėtų skaičiumi tikėti žodžiu. Įrankis išrenka kiekvieną sakinį, kuriame nurodytas skaičius, procentas, kaina, data, reitingas („pirmas“, „didžiausias“, „first“, „largest“) ar kvalifikacija („sertifikuota“, „partneris“, „apdovanojimas“), ir ieško jo faile `runs/notes/facts.md` bei kituose darbo katalogo failuose ar URL adresuose, pateiktuose per `sources`. Skaičiai pirmiausia suvienodinami, todėl `12 400`, `12,400` ir `12400` yra tas pats skaičius, `2,5 %` sutampa su `2.5%`, o `200+` atitinka `200`. Kiekvienas teiginys grįžta kaip `supported` su šaltiniu, jo eilute ar simbolių intervalu ir ištrauka, kaip `number_differs`, kai šaltinis nurodo 180 ten, kur juodraštis sako 200 (abu skaičiai pateikiami žmogui nuspręsti), arba kaip `unsupported`. Nė vienas teiginys nepažymimas kaip pagrįstas be jį patvirtinančios ištraukos. Rinkodaros receptas paleidžia jį tekstui prieš tyrimą, o tikrintojas jį turi kaip skaitymo įrankį, tad nepagrįstas teiginys tampa pastaba, o ne paskelbtu sakiniu.

Dabar jis daro daugiau nei sugretina skaičių su ištrauka. Su `check_scope: true` jis perskaito pagrindžiančios ištraukos sąlygas („tik“ / „only“, metus ar laikotarpį, vietą, apklaustą imtį, laboratorinę ar bandymo sąlygą) ir, kai juodraštis jas praleidžia, teiginys grįžta kaip `supported_scope_narrower` su išvardytomis praleistomis sąlygomis, o juodraštis, apibendrinantis plačiau nei šaltinis („visi“, „every“, „always“), gauna radinį `misleading_inference`. Nurodžius `quote_sources` (interviu užrašus), kiekvienas kabutėse esantis šešių ar daugiau žodžių sakinys tikrinamas pažodžiui ir grąžinamas kaip pagrįstas, pakeistas arba nepagrįstas, o šalia citatos nurodytas vardas turi būti prie tos ištraukos. Kvalifikacijai, tokiai kaip „Google Premier Partner“ ar „ISO 9001“, reikia šaltinio, kuriame įvardytas ją suteikęs subjektas, ir prie kiekvienos pagrįstos nurodoma, kas už jos stovi; `own_domains` kiekvieną šaltinį pažymi kaip savą ar trečiosios šalies ir pažymi sakinį, kuriame nepasakyta, kieno skaičius naudojamas. `mode: reply` patikrina atsakymą gijoje punktas po punkto pagal cituojamus faktus, atstovą ir gijos adresą; `mode: price_anchor` patikrina, ar perbraukta palyginamoji kaina yra įrodymuose, atitinka juodraštyje vartojamus žodžius ir buvo iš tikrųjų taikyta. Kiekvienas rezultatas taip pat turi paruoštą įklijuoti pagrįstų teiginių `fact_sheet` su šaltiniais ir `corrections` sąrašą – po vieną eilutę kiekvienam besiskiriančiam ar nepagrįstam teiginiui; HTML šaltiniai indeksuojami pagal lentelės eilutes ir paveikslėlių alt tekstą.

`reach_overlap` dabar skaito ir kelis neprivalomus kanalo laukus, o paprastas kvietimas lieka nepakitęs. Nurodyk kiekvieno kanalo `source` (paid, owned, earned) ir `required_reach` – gausi `by_source` ribas ir kainą pagal kiekvieną grupę, uždirbtos sklaidos dalį parodymuose ir `unpaid_sustains_required` (yes / no / unknown) su sakiniu, kiek dar turi pasiekti mokama sklaida; nurodyk `role` (brand, activation, both) – kaštų pasiskirstymas palyginamas su 60/40 atskaitos tašku. Kanalo `addressable_pct` prideda pasiekiamų pirkėjų skaičių, kainą už tūkstantį žmonių, kurie iš tikrųjų gali pirkti, ir naują kanalų eilę; `category_buyers` bendrą pasiekiamumą parodo kategorijos pirkėjų, o ne tavo sekėjų atžvilgiu, o `existing_customer_share` atskiria esamų klientų pasiekiamumą nuo būsimų pirkėjų. `frequency_distribution` kartu su `cap` duoda `cap_scenario`: atlaisvintus parodymus ir biudžetą, dar nepasiektų žmonių rezervą ir papildomą pasiekiamumą kaip ribas; vien vidutinis dažnis atmetamas. Blokas `measure` neleidžia sudėti peržiūrų, parodymų ir žmonių: peržiūromis matuojamas kanalas į bendrą skaičių neįtraukiamas ir įvardijamas lauke `comparability`.

`media_plan` – vienas įrankis su `mode`, atliekantis medijos biudžeto aritmetiką, kurią darbuotojas kitaip darytų prozoje: `ledger` sumuoja biudžeto eilutes pagal kanalą, kaštų tipą ir vaidmenį ir parodo kiekvieno kanalo dalį dviem pagrindais; `sov` iš išlaidų ir rinkos dalies apskaičiuoja balso dalį, rinkos dalį ir perteklinę balso dalį procentiniais punktais bei kiek kainuotų siekiamas ESOV; `budget` įkainoja veiklas pagal tikslą (padengimas, kaina už rezultatą, atsarga, pirmiausia karpytinos veiklos) ir palygina prekės ženklo / aktyvacijos pasiskirstymą su šaltinį turinčiu B2B ar B2C etalonu, kiekvieną pataisymą pritaikydamas tik kartą; `offers` sulygina medijos pasiūlymus vienu vardikliu ir atsisako reitinguoti CPA pasiūlymą, kurio mokėjimo įvykis neįvardytas; `volume` sudaugina populiaciją, penetraciją, pirkimus vienam pirkėjui ir vienetus viename pirkime ir sutikrina su deklaruota apimtimi; `calendar` išdėsto metus kaip 52 nuolatinės veiklos savaites su pikais apie žinomas Lietuvos progas ir pažymi persidengimus, per trumpą pasirengimo laiką ir biudžeto ribą; `channel_fit` tikrina, ar naujai platformai yra šaltinį turinčių įrodymų ir ar užtektų komandos valandų; `classify` išlaidų eilutes skirsto į prekės ženklo, aktyvacijos ar mišrias pagal pačios kūrybos tekstą, niekada – pagal kanalą.

`campaign_calc` – vienas įrankis su trylika režimų kampanijos aritmetikai, kurią darbuotojas kitaip skaičiuotų prozoje; kiekvienas grąžina skaičius, jų `method`, ko trūksta (`missing`), `warnings` ir vieno sakinio `summary`. `lift_compare` palygina mėginių, kuponų ar geografinių šakų kainą už pirkėją su Wilsono intervalu, o prieauginiu pirkėją vadina tik tada, kai nurodyta `baseline_buyers`; `referral_loop` iš kvietimų × priėmimo × aktyvacijos apskaičiuoja K, stiprinimą 1/(1−K), vartotojų skaičių po n ciklų ir lūžio tašką paskatai; `seeding_compare` išrikiuoja pasėtas ir organines šakas; `bass` prognozuoja įsisavinimą iš rinkos dydžio ir arba šešių ar daugiau istorijos laikotarpių, arba pateiktų p ir q, pažymėdamas numatytąsias reikšmes; `buyer_overlap` iš pirkėjų × prekių ženklų eilučių apskaičiuoja penetraciją, pirkimo dubliavimąsi ir galimas rinkos pertvaras; `network_value` parodo pasiekiamų sandorio šalių skaičių po integracijos pagal tiesinį ir Metcalfe'o modelį, abu pažymėtus kaip modelius; `commission_compare` parodo, kiek kiekvienas planas moka už pardavimą ir už pardavėją, ir įvardija bendrojo pelno procentą, kuriam esant atlygio fondas nesikeičia; `catchment_compare` įvertina vietas su įvardytais svoriais ir laimėtojos jautrumu jiems; `account_penetration` atskiria augimą, likusį esamose sąskaitose, nuo naujų klientų; `mental_availability` parodo, kuriose pirkimo situacijose prekės ženklas dingsta iš neskatinto prisiminimo; `benchmark_fit` patikrina išorinį vidurkį pagal prekės ženklo šaką, rinką, biudžetą, tikslą ir laikotarpį ir grąžina intervalą, niekada – pliką tašką; `line_metrics` ir `audience_panel` išmatuoja teksto ritmą ir respondentų aprėptį. Neįmanomi duomenys atmetami aiškiu sakiniu, o trūkstamas skaičius niekada nespėjamas.

### Tavo paties duomenys

`funnel_analyze` perskaito darbo kataloge esantį CSV arba XLSX failą ir paverčia jį piltuvėliu su tikrais skaičiais: kiek yra kiekviename etape, kokia dalis išlieka nuo ankstesnio ir nuo pirmojo etapo, kuriame žingsnyje prarandama daugiausia ir kokia bendra konversija. Plačiam failui nurodyk etapų stulpelius eilės tvarka (`stages`), o duomenims po vieną eilutę įvykiui – `stage_column` kartu su `count_column` arba `id_column`; pridėk `date_column` su `date_from` / `date_to`, kad apribotum laikotarpį, ir `group_by`, kad gautum piltuvėlį kiekvienam kanalui, kampanijai ar kohortai ir blogiausios grupės pavadinimą. Rezultate nurodomos iš tikrųjų aprėptos datos ir panaudotų eilučių skaičius, o ne tos datos, kurių prašei. Visa, ko duomenys negali atsakyti – tušti langeliai, tekstas vietoj skaičiaus, neįskaitomos datos, tuščias laikotarpis, neegzistuojantis stulpelis – išvardijama skiltyje `missing`, o ne spėjama, o jei vėlesnis etapas didesnis už ankstesnį, pateikiamas įspėjimas. Vieno sakinio `summary` parašytas taip, kad jį būtų galima cituoti ataskaitoje be pakeitimų.

Dabar jis lygina ir grupes: su `group_by` (arba `cohort_by` / `periods`, kurie grupę išveda iš datos) jis atlieka dviejų proporcijų z testus etape `compare_stage` pirmosios grupės atžvilgiu ir grąžina skirtumą procentiniais punktais, 95 % pasikliautinąjį intervalą, p reikšmę ir verdiktą better, worse, no_difference arba insufficient_data (mažiau nei `min_n`, numatytai 100), o kai grupių datų langai nesutampa, įspėja; `cost_per_group` ar `cost_column` prideda grynąsias sąnaudas, kainą vienam konvertavusiam ir bendrą visos kampanijos rodiklį, o `metrics` ir `share_of` – grupių vidurkius ir atrankos šališkumo patikrą. `lag_from` / `lag_to` dienų skaičių tarp dviejų datų suskirsto į intervalus su mediana ir dalimi po nulinės dienos, o `maturity_days` leidžia lyginti skirtingo amžiaus grupes; `bucket_by` laiko žymą paverčia valandų ar savaitės dienų intervalais ir įvardija mažiausią intervalų rinkinį, apimantį `cover_share` paskutinio etapo apimties. `derived_stages` iš lentelės po vieną eilutę vartotojui sudaro piltuvėlį pagal taisykles, dviejų stulpelių `group_by` pateikia matricą „kas kam rekomenduoja“, o `paid_at_stage` suskaičiuoja apmokėtus vienetus, nepasiekusius paskutinio etapo. Penki režimai tuos pačius failus skaito kitiems klausimams: `buyer_classes` (akcijos vienetai pagal ankstesnį pirkimo dažnį), `offers` (nuolaidų dalis ir tik su nuolaida perkantys klientai pagal laikotarpius), `trend` (naujo pirkėjo kaina kiekvienu paleidimo laikotarpiu), `recruits` (naujo produkto pirkėjai, nauji prekės ženklui) ir `categories` (kategorijų stulpelio skaičiai, dalys ir Pareto rinkinys).

### Vaizdai

`vision_analyze` su `brief` (stilius – nuotrauka / iliustracija / diagrama, objektas, auditorija, į kurią turi būti panašūs parodyti žmonės, `must_include`, `must_not`) patikrina sugeneruotą ar pateiktą vaizdą pagal užduotį ir grąžina `brief_check: pass` arba `fail` su neatitikimais, todėl saulėlydžio pora vyresnių nei penkiasdešimties moterų auditorijai pagaunama dar prieš reklamai išeinant; receptai po nesėkmės vaizdą generuoja iš naujo vieną kartą, o diagramas piešia skriptu, ne vaizdų modeliu.

`image_generate` nupiešia PNG „OpenRouter“ vaizdų modeliu ir įrašo į `deliverables/` (`prompt`, neprivalomi `path`, `reference_path` — esamas vaizdas redagavimui ar stiliui, `aspect_ratio` — `1:1`, `9:16`, `16:9` ir pan.). Numatytasis modelis `google/gemini-nano-banana-2.1`; kitą prisegk per `ROCKY_IMAGE_MODEL` (pvz., `google/gemini-3-pro-image`, `openai/gpt-5.4-image-2`). Jei pirmasis modelis atsisako, bandomas kitas. Kiekvienas vaizdas įtraukiamas į paleidimo kvitą kaip bet kuris kitas kvietimas. `vision_analyze` leidžia darbuotojui patikrinti rezultatą pagal jo brief'ą.

### MCP serveriai

Rocky kalba MCP per stdio. Serverius surašyk į `<katalogas>/.rocky/mcp.json` (projektas) arba `~/.rocky/mcp.json` (visur); to paties pavadinimo serveriui projekto failas laimi. Priimamas ir `mcpServers` (Claude Code / Cursor stilius).

```json
{
  "servers": {
    "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
    "notion": {"command": "notion-mcp", "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"}},
    "old": {"command": "x", "disabled": true}
  }
}
```

Kiekvieno serverio įrankiai darbuotojams matomi kaip `mcp__<serveris>__<įrankis>` su paties serverio schema. Serveriai startuoja pirmą kartą pakvietus ir sustoja paleidimui pasibaigus; nepasileidęs serveris praleidžiamas ir įvardijamas `mcp` įvykyje.

### Projekto instrukcijos

Jei darbo kataloge yra `ROCKY.md`, `AGENTS.md` arba `CLAUDE.md` (pirmas rastas, iki 8 000 ženklų), jis įterpiamas į kiekvieno vaidmens prompt'ą kaip **PROJECT INSTRUCTIONS** — konvencijos, tonas, keliai, ko neliesti.

„Office“ / PDF (`.xlsx` `.docx` `.pptx` `.pdf`): darbuotojas privalo `skill_view` atitinkamą įgūdį, tada failą kurti per **openpyxl / python-docx / python-pptx / reportlab** iš `bash`. Šių dvejetainių failų niekada nerašo `write_file`.

---

## 14. Įgūdžiai

Įmontuoti: `xlsx`, `docx`, `pptx`, `pdf`, `deep-research`, `marketing-pipeline`, `marketing-strategy`, `marketing-numbers`, `customer-research`, `pr-response`, `web-testing`.

`web-testing` nurodo, kaip darbuotojas tikrina svetainę, formą, srautą ar programą naršyklės įrankiais: po eilutę kiekvienam žingsniui faile `runs/notes/walkthrough.md` (URL, veiksmas, kas pasirodė, sekundės, pinigų paminėjimai), kas laikoma nesėkme registracijai, tuščiai paskyrai, eksportui, atšaukimui, formų klaidoms, paieškai be rezultatų, atsiskaitymui, piko apkrovai, puslapio greičiui, užduočiai telefonu, savitikros įrankiui, vaizdo įrašo išlaikymui, perpildytam srautui ir vaizdo atsiminimui, ir ko begalvė naršyklė negali, todėl paspaudimas ar įvestis atliekami per „Playwright“ MCP įrankį arba rinkodarininko ekrano nuotrauką, o ne spėjami. `marketing-strategy` laiko vadovėlio sprendimo taisykles, kurių kodas negali išspręsti (pozicionavimas, portfelis, kainos kėlimo perėjimas, aptarnavimo elgsena, kanalų konfliktas, gamybos įgyvendinamumas ir kt.), kiekvieną su šaltinio puslapiais; receptai ją įkelia, kai tikslas atitinka situaciją. `marketing-numbers` pasako, kuris skaičiuotuvas pateikia kiekvieną skaičių. `customer-research` nurodo, ką apklausti dėl pirkinio (operatorių, prižiūrėtoją, biudžeto savininką, nevartotojus, prekybos pirkėjus), kokius klausimus užduoti lietuviškai ir angliškai ir kaip fiksuoti klientų perdirbimus, naudojimo aplinką ir apklausos blokus, kad vaidmuo be įrodymų būtų pažymėtas kaip trūkstamas, o ne išgalvotas. `pr-response` apima viešus atsakymus į kritiką toje pačioje gijoje su įvardytu atstovu ir šaltiniais pagrįstais faktais, žurnalistų vizitus su nepriklausomais klausimais ir atskleistomis išlaidomis, darbuotojų bendravimo socialiniuose tinkluose gaires ir bendruomenės ar talkininkų programas, sukurtas pagal tai, kodėl žmonės dalyvauja.

Rocky iš `~/.claude/skills` dar įkelia **tyrimų + rinkodaros** leidžiamųjų sąrašą (copywriting, voc-to-copy, meta-ads-copy, …) ir lietuvių kalbos įgūdžius `lietuviu-kalbos-rasyba` bei `turtinga-lietuviu-kalba`, jei jie ten yra; lietuvišką tekstą rašančiam darbuotojui liepiama pirmiausia atsiversti rašybos įgūdį. Asmeniniai įgūdžiai lieka už borto. Papildomi katalogai:

```bash
export ROCKY_SKILLS=/kelias/iki/kitu/igudziu
```

„Facebook“, „Instagram“ ir „Meta“ reklamų tikslai vykdomi pagal įmontuotą `ads` receptą: **faktai → reklamos → vizualai**, be skaidrių ir lentelių. Žingsniai nustatyti iš anksto, todėl planuotojas kviečiamas tik perplanuojant. Rezultatas — `deliverables/ads.html`, vienas savarankiškas puslapis (jį atveria `rocky open`): kiekviena reklama atrodo kaip sraute, nukirpta ten, kur nukerpa srautas, su kopijavimo mygtukais, vizualo atsisiuntimu ir antspaudu, ar ji paruošta. Prie kiekvienos reklamos puslapis nurodo, ką pirkėjas gauna, kiek tai kainuoja ir kur tai gauna, kad pažadą galėtum palyginti su pasiūlymu; spraga yra įspėjimas. Taip pat rodomas kiekvieno segmento apytikslis dydis, perkamoji galia ir kaina jį pasiekti, o trūkstami duomenys pažymimi įspėjimu, nes siaura niša gali neatsipirkti. Pirma reklama yra kontrolinė — dabartinė svetainės žinutė, o kitos kalba apie rezultatus, kuriuos produktas jau duoda, todėl rinkinį galima iškart testuoti. Po reklamomis išvardyta, ko pirkėjai tikisi iš bet kurio šios srities pardavėjo, koks minimumas ir ar prekės ženklas jį pasiekia; nepasiektas minimumas pažymimas viršuje, kad jį sutvarkytum prieš mokėdamas už reklamą. Puslapis nurodo, ar kampanija parduoda daugiau esamoje rinkoje, ar atveria naują, ir įspėja, kai naujai rinkai nėra įrodymų, kad jos pirkėjus galima pasiekti. Kai reklamos siūlo daugiau nei vieną produktą, sąrašas pasako, ką siūlyti iš kiekvienos reklamos atėjusiam žmogui. Prie kiekvieno radinio yra **Kodėl?** su paaiškinimu ir šaltiniu, veiksmas ir galimybė radinį atmesti. **Taisyti** paleidžia vieną tęstinį darbą, kurio lubos 0,50 €, ir pakeičia tik tą vietą. Per `rocky serve` puslapis atsidaro skiltyje **Result**, o mygtukas taisymą paleidžia ten pat; atidarius kaip failą, jis nukopijuoja `rocky resume` komandą. Radiniui, kurio Rocky pataisyti negali, pavyzdžiui, nepasiektam minimumui, siūloma **Pažymėti kaip sutvarkytą**. Darbuotojai reklamas skelbia įrankiu `ad_board`, o kiekvieną kvietimą tikrina kodas: skaičius tekste turi turėti `claims` įrašą, kurio šaltinio nuoroda yra `runs/notes/facts.md`; kitaip reklama pažymima kaip taisytina, o darbuotojui tiksliai pasakoma, ką pakeisti. Tekstas, ilgesnis, nei rodo srautas, ir absoliutūs pažadai yra įspėjimai, dėl kurių sprendi tu. Puslapis įrašomas, kai tik tekstai paruošti, ir atnaujinamas atsiradus vizualams, todėl net vėliau nutrūkęs paleidimas palieka reklamas; jei tikrintojas nespėjo, o automatiniai patikrinimai švarūs, paleidimas baigiasi `passed_with_notes`.

Kiti rinkodaros tikslai ir reklamų tikslai, kuriuose minimos skaidrės ar lentelė (`pptx`, `xlsx`, `deck`), planuojami kaip **tyrimas → VOC → tekstas → pptx + xlsx → vizualai** — tai įmontuotas `marketing` receptas. Vizualų žingsnis parašo `deliverables/visual-brief.md` (po bloką kiekvienam vizualui: formatas, žinutė, kompozicija, prekės ženklo hex spalvos, teksto užrašas), tada per `image_generate` kiekvieną bloką paverčia `deliverables/visual-1.png`, `visual-2.png`, …

### Receptai

Receptas — plano griaučiai, kuriuos Rocky priverstinai taiko, kai tikslas atitinka: sutvarkyti žingsniai, `depends_on`, galutinio failo kelias ir raktažodžių užuominos. Failai — TOML:

- įmontuoti: `rocky/recipes/*.toml` (`ads` — faktai, reklamos, vizualai viename puslapyje; `marketing` — tyrimas, VOC, tekstas, skaidrės + lentelė, vizualai)
- bendri: katalogai iš `ROCKY_RECIPES=/kelias/a,/kelias/b`
- šio katalogo: `<katalogas>/.rocky/recipes/*.toml` (tuo pačiu pavadinimu laimi)

Kiekvienas žingsnis gali deklaruoti `writes = ["runs/notes/research.md"]` — failus, kuriuos privalo palikti diske. Kai darbuotojas sustoja be jų, Rocky jį paragina (daugiausia dukart, `nudge` įvykis); jei failų vis tiek nėra, žingsnis tikrintojui pateikiamas kaip **INCOMPLETE**, kad tolesni žingsniai ir verdiktas negalėtų apsimesti, jog darbas atliktas. Planuotojo žingsniai `writes` sąrašą gauna irgi.

Atitikimo tvarka: `recipe:<pavadinimas>` bet kur tiksle → receptas, atpažįstamas vien pagal žodžius (`ads`) → įvertinta sritis → raktažodžiai. Jei planuotojas jau dengia visus privalomus etapus, planas paliekamas; kitaip recepto žingsniai jį pakeičia. Darbuotojai gali `recipe list / view / save` — išsaugotas receptas naudojamas nuo **kito** paleidimo, tad peržiūrėk jį. Formatas — `rocky/recipes/marketing.toml`.

---

## 15. Biudžeto užuominos

| Atrodo kaip | Tipinis spėjimas prieš 1 EUR |
| --- | --- |
| Vienos eilutės užduotis (`trivial`) | Be kritiko, be planuotojo: įvertinimas → darbuotojas → tikrintojas, dažniausiai gerokai mažiau nei 0,01 EUR |
| Trumpas perrašymas, be interneto | Telpa į lubas — klausimo nėra |
| Sudėtingas tyrimas, reklamos, keli failai | Rocky klausia, ar pakelti **tik šiam paleidimui** |

`--budget 10` komandinėje eilutėje praleidžia klausimą, jei spėjimas mažesnis nei 10 EUR.

Spėjimas **kalibruojamas pagal šio katalogo istoriją**: Rocky ima medianą *faktinė ÷ spėta* iš baigtų paleidimų (to paties sudėtingumo, jei tokių bent du; kitaip visų; ribojama 0,5–4×) ir tuo daugikliu pakoreguoja lentelės reikšmę. `assessed` eilutė rodo `~€9.10 (×1.6 from 4 past runs)`, kai daugiklis pritaikytas; `--json` įvertinime pateikia `estimate_base_eur` ir `calibration`. Kiekvienas baigtas paleidimas į savo `meta.json` įrašo `eur`, `estimated_eur` ir `complexity`, kad kitas spėjimas iš jo pasimokytų.

Kol darbuotojai dirba, Rocky vieną kartą įspėja (`budget_warn`, etapas `half`), kai **išleista 50 % lubų, o galutinio failo diske dar nėra** — tinkamas momentas prisijungti ir nuspręsti, ar leisti tęsti. arba `rocky stop` jį nutraukti.

`rocky cost` sumuoja katalogo `meta.json` failus: bendras ir laikotarpių išlaidas, išlaidas vienam naudojamam rezultatui ir kiek nuėjo paleidimams, pasibaigusiems `unmet` / `error` / `crashed` / `stopped`.

---

## 16. Trikčių šalinimas

| Požymis | Sprendimas |
| --- | --- |
| `OPENROUTER_API_KEY is missing` | Paleisk `rocky` terminale ir įklijuok raktą arba įrašyk jį į `~/.env`; tada `rocky doctor` |
| `OpenRouter rejected the API key (401)` | Raktas neteisingas arba atšauktas. `rocky doctor`, tada naujas raktas iš openrouter.ai/keys į `~/.env` |
| `OpenRouter account has no credits (402)` | Papildyk sąskaitą openrouter.ai/credits |
| `Cannot reach OpenRouter after 4 attempts` | Tinklas, DNS ar tarpinis serveris. Katalogo užklausa ir kiekvienas užbaigimas pirmiausia kartojami su pauzėmis; patikrink ryšį, tada `rocky doctor` |
| `ModuleNotFoundError: rocky` | Įdiek iš naujo be `--editable` (Python 3.13) |
| `rocky: command not found` | `uv tool install --force --reinstall .` iš klono; `~/.local/bin` turi būti `PATH` |
| TUI „nieko nevyksta“ | Palauk, kol atsisiųs katalogas; būsenos eilutė rodo „starting“, tada „fetching models“. Jei taip ir lieka, patikrink raktą su `rocky doctor`. |
| Kopijavimas TUI neveikia | Pažymėk tekstą, tada Cmd+C. Išeik su **q**, ne Ctrl+C. |
| Paleidimas mirė uždarius langą | Neturėtų. `rocky ps` — jei būsena `crashed`, skaityk `.rocky/runs/<id>/worker.log`, tada `rocky resume` |
| Paleidimas baigėsi `error` / `crashed` pusiaukelėje | `rocky resume [id]` — tęsia nuo išsaugoto plano ir jau parašytų failų |
| Tikrintojas „beveik“ praleidžia | Ciklą sužlugdo ir perplanavimą sukelia tik **blokuojantis** trūkumas. Vien pastabos paleidimą baigia kaip `passed_with_notes` su failu. Po paskutinio ciklo įvertis ≥ 0,7 su failu diske ir be nieko blokuojančio taip pat baigiasi `passed_with_notes`. |

---

## 17. Atmintis

Anksčiau kiekvienas naujas tikslas prasidėdavo nuo nulio. Dabar Rocky tame pačiame darbo kataloge laiko Hermes stiliaus atmintį.

| Saugykla | Kur | Riba | Kas ten priklauso |
| --- | --- | --- | --- |
| **USER.md** | `~/.rocky/memories/USER.md` | 1 375 ženklai | Tu: kalba, tonas, įpročiai |
| **MEMORY.md** | `<katalogas>/.rocky/MEMORY.md` | 2 200 ženklų | Šis katalogas: keliai, konvencijos, išmokti faktai |
| **Paskutiniai paleidimai** | `.rocky/runs/*/meta.json` | 5 paskutiniai baigti | Ankstesni tikslai ir tikrintojo santraukos |

USER + MEMORY **užšaldomi paleidimo pradžioje** ir įterpiami kritikui, planuotojui, darbuotojams ir tikrintojui. Įrašai per `memory` įrankį į diską patenka iškart, bet prompt'e pasirodo **kitą** paleidimą (tas pats užšaldymas kaip Hermes, kad paleidimas neperrašinėtų savo paties sistemos prompt'o).

`runs_search` peržvelgia senesnius paleidimus, kai naudotojas sako „tas straipsnis“ / „paskutinės skaidrės“. Tęstiniai tikslai tame pačiame kataloge („dabar iš to padaryk pptx“) turėtų tęsti paskutinį galutinį failą, nebent aiškiai jį keiti.

Nepilk į atmintį transkriptų. Tik glausti faktai. Jei failas pilnas, pirmiau pakeisk ar pašalink įrašą.

Namų katalogo pakeitimas testams ar neįprastoms aplinkoms: `ROCKY_HOME=/kelias`.

---

## 18. Kas Rocky nėra

Tai ne kodavimo aplinka kaip Claude Code ar Codex. Tos laiko vieną stiprų modelį ilgame cikle. Rocky — **sudėties + biudžeto + tikrinimo** dirbtuvė: pigios rankos, nepriklausomas tikrintojas, eurų lubos. Naudok tyrimams, tekstams, skaidrėms ir apribotam autonomiškam darbui kataloge. Sunkiam repozitorijos refaktoringui dažniausiai geriau tinka specializuotas kodavimo agentas.

---

## Licencija

MIT. Autorius: Aurimas Gircys.
