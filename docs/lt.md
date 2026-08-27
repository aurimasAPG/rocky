# Rocky — kaip naudotis

Rocky — „OpenRouter“ agentų orkestratorius. Duodi tikslą ir katalogą. Jis iš gyvo katalogo parenka modelius, suskaido darbą, naudoja įrankius ir patikrina rezultatą. Numatytosios išlaidų lubos — **1 EUR vienam paleidimui**.

Komanda — `rocky`. `orche` yra tylus tos pačios programos sinonimas.

Pilnas angliškas vadovas: `rocky help` (tas pats tekstas kaip `rocky/HELP.md`).

## Diegimas

Reikia [uv](https://docs.astral.sh/uv/) ir Python 3.11 ar naujesnės versijos.

```bash
git clone <ši-saugykla>
cd <repo>
uv tool install --force --reinstall .
```

Į `PATH` įtrauk `~/.local/bin`. Pakeitus kodą, tą pačią komandą paleisk dar kartą iš klono katalogo.

**Python 3.13:** nenaudok `pip install -e .`. Paslėpti `.pth` failai praleidžiami ir `rocky` neimportuos.

Raktas (niekada neįrašyk į git) faile `~/.env`, projekto `.env` arba aplinkoje:

```
OPENROUTER_API_KEY=sk-or-...
```

Naršyklės įrankiai (neprivaloma):

```bash
uv tool install --force --reinstall ".[browser]"
playwright install chromium
```

## Pirmas paleidimas

```bash
mkdir -p /tmp/rocky-demo
cd /tmp/rocky-demo
rocky
```

Turėtum pamatyti auksaspalvio retriverio plakatą. Įrašyk užduotį, spausk Enter.

Vienu kartu:

```bash
rocky -d /tmp/rocky-demo "parašyk trijų sakinių santrauką į summary.md"
```

Trys langai (planas / darbas / tikrinimas):

```bash
rocky tui -d /tmp/rocky-demo
```

## Komandos

| Komanda | Ką daro |
| --- | --- |
| `rocky` | Interaktyvi CLI. Įrašai tikslą, stebi ciklą, rašai kitą. |
| `rocky "tikslas"` | Paleidžia foninį darbą ir prisijungia. |
| `rocky tui` | Tas pats variklis, trys langai (`q` atjungia). |
| `rocky ps` | Sąrašas šio katalogo paleidimų. |
| `rocky attach [id]` | Vėl prisijungti (Enter atjungia). |
| `rocky serve` | Komandos tinklo sąsaja, http://127.0.0.1:8787 |
| `rocky help` | Pilnas angliškas vadovas |
| `rocky route "tikslas"` | Tik įvertina ir parenka modelius (be įrankių) |

Parametrai: `--dir`, `--budget` (eurais, numatytai **1,00**), `--max-loops` (numatytai 4).

Kiekvienas paleidimas spausdina **kvitą**: įėjimo / išėjimo / viso žetonai, USD, EUR (3 skaitmenys po kablelio).

## Atjungti ir vėl prisijungti

Darbas eina **atskiru procesu**. Uždaręs žiūryklę jo nenužudai.

- CLI: **Enter** atjungia.
- TUI: **q**, tada Enter (arba Ctrl+Q).

Sugrįžti: `rocky ps`, `rocky attach`, `rocky tui --attach ID`.

Žurnalai: `.rocky/runs/<id>/`.

## Saugumas

Rocky **visiškai autonomiškas** kataloge `--dir`. Failų įrankiai neleidžia kelių už to katalogo ribų. Rašant failus nėra klausimo „ar tikrai?“.

`bash` **nėra smėlio dėžė**. Procesas startuoja darbo kataloge, bet komanda vis tiek gali išeiti (`cd`, `curl` ir t. t.). Nenukreipk `-d` į namų katalogą.

`rocky serve` numatytai klausosi `127.0.0.1` (tik ši mašina). `rocky serve --host 0.0.0.0` **be** `ROCKY_TOKEN` atsisako paleisti: procesas baigiasi, serveris neprisijungia. Prieš atverdamas sąsają nustatyk `ROCKY_TOKEN` ir, jei reikia, `ROCKY_WORKSPACES`.

Raktą laikyk `~/.env` arba projekto `.env`, kurio git nemato.

## Atmintis

Kitas tikslas tame pačiame kataloge tęsia ankstesnį. Paleidimo pradžioje užšaldoma (kaip Hermes):

- `~/.rocky/memories/USER.md` — kas tu (1 375 ženklai)
- `<katalogas>/.rocky/MEMORY.md` — šio projekto faktai (2 200 ženklų)
- paskutiniai 5 baigti paleidimai (tikslas + santrauka)

Darbuotojas gali `memory` (add / replace / remove) ir `runs_search`. Į diską rašoma iškart; į promptą pateks **kitą** paleidimą.

## Kas vyksta paleidus

1. Pigus modelis įvertina sudėtingumą.
2. Jei spėjama kaina viršija lubas, Rocky klausia, ar šiam paleidimui jas pakelti (`yes`, `keep` arba skaičius).
3. Iš gyvo katalogo parenkamas trejetas: planuotojas, darbuotojas, tikrintojas.
4. Kritikas užpuola užduotį ir nustato kokybės kartelę.
5. Planuotojas skaido darbą (`depends_on`, lygiagrečios bangos).
6. Darbuotojai — po pokalbį kiekvienai daliai, bendras katalogas. Numatytasis modelis — Gemini 3.7 Flash (`ROCKY_WORKER` pakeičia).
7. Tikrintojas vertina **visą** rezultatą. „Beveik gerai“ — nesėkmė, planas perskirstomas.

Rinkodaros / skelbimų / tekstų tikslai planuojami seka **tyrimas → VOC → tekstas → pptx + xlsx**.

## Licencija

MIT. Autorius: Aurimas Gircys.
