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

Iš puslapio paleistas darbas — **tas pats foninis darbas** kaip `rocky "tikslas"`: jis matomas `rocky ps`, o `rocky attach`, `rocky stop`, `rocky resume` ir `rocky open` su juo veikia. Kiekvienas atidarytas skirtukas seka paleidimo `events.jsonl` savo žymekliu, tad antras žiūrovas ar puslapio perkrovimas mato visą paleidimą. Puslapis rodo biudžeto pasiūlymus, `clarify` klausimus (atsiranda atsakymo laukelis — darbas laukia apie 10 minučių, tada tęsia su saugiu numatytuoju atsakymu), strigtis, paraginimus, nebaigtus žingsnius, verdiktą „blokuojantys / pastabos“ ir galutinį perdavimą (rezultato kelias, dalinis darbas, pastabos). **Stop the run** nutraukia darbą kaip `rocky stop`. Sąrašas apačioje rodo katalogo paleidimus; spustelėk vieną, kad jį sektum.

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

`bash`, `read_file`, `write_file`, `append_file`, `patch`, `list_dir`, `grep`, `todo`, `web_search`, `web_fetch`, `browser_navigate` / `browser_snapshot` / `browser_close`, `vision_analyze`, `image_generate`, `clarify`, `skills_list`, `skill_view`, `office_read`, `memory`, `runs_search`, `recipe`, `ad_board`, plius bet kurie tavo sukonfigūruoti **MCP** įrankiai (žemiau).

### Vaizdai

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

Įmontuoti: `xlsx`, `docx`, `pptx`, `pdf`, `deep-research`, `marketing-pipeline`.

Rocky iš `~/.claude/skills` dar įkelia **tyrimų + rinkodaros** leidžiamųjų sąrašą (copywriting, voc-to-copy, meta-ads-copy, …) ir lietuvių kalbos įgūdžius `lietuviu-kalbos-rasyba` bei `turtinga-lietuviu-kalba`, jei jie ten yra; lietuvišką tekstą rašančiam darbuotojui liepiama pirmiausia atsiversti rašybos įgūdį. Asmeniniai įgūdžiai lieka už borto. Papildomi katalogai:

```bash
export ROCKY_SKILLS=/kelias/iki/kitu/igudziu
```

„Facebook“, „Instagram“ ir „Meta“ reklamų tikslai vykdomi pagal įmontuotą `ads` receptą: **faktai → reklamos → vizualai**, be skaidrių ir lentelių. Žingsniai nustatyti iš anksto, todėl planuotojas kviečiamas tik perplanuojant. Rezultatas — `deliverables/ads.html`, vienas savarankiškas puslapis (jį atveria `rocky open`): kiekviena reklama atrodo kaip sraute, nukirpta ten, kur nukerpa srautas, su kopijavimo mygtukais, vizualo atsisiuntimu ir antspaudu, ar ji paruošta. Darbuotojai reklamas skelbia įrankiu `ad_board`, o kiekvieną kvietimą tikrina kodas: skaičius tekste turi turėti `claims` įrašą, kurio šaltinio nuoroda yra `runs/notes/facts.md`; kitaip reklama pažymima kaip taisytina, o darbuotojui tiksliai pasakoma, ką pakeisti. Tekstas, ilgesnis, nei rodo srautas, ir absoliutūs pažadai yra įspėjimai, dėl kurių sprendi tu. Puslapis įrašomas, kai tik tekstai paruošti, ir atnaujinamas atsiradus vizualams, todėl net vėliau nutrūkęs paleidimas palieka reklamas; jei tikrintojas nespėjo, o automatiniai patikrinimai švarūs, paleidimas baigiasi `passed_with_notes`.

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
