# ladok-vakt

**Få mejl när Ladok-anmälan öppnar, innan fönstret stänger.**

`ladok-vakt` bevakar kursregistrering och tentamensanmälan i Ladok.

English: [README.en.md](README.en.md).

---

## Varför ladok-vakt?

Ladok erbjuder inte epost-notiser i sin studenttjänst, dessa skript kringgår detta.

`ladok-vakt` hämtar datum för anmälning till kurs och examination direkt från [Ladok](https://student.ladok.se/) och påminner användaren via epost eller annan kanal.

## Snabbstart

### Förutsättningar

- Python 3.8+
- Chrome eller Chromium
- Python-paketet `websockets`

### Installera och prova

```bash
git clone https://github.com/vhsgreed/ladok-vakt && cd ladok-vakt
python3 -m pip install websockets

# Starta Chrome med en egen profil och logga in i Ladok där en gång
flatpak run com.google.Chrome \
  --user-data-dir=$HOME/.config/ladok-vakt-chrome \
  --remote-debugging-port=9233
# Icke-Flatpak: google-chrome med samma flaggor

# Prova att läsa från Ladok
python3 -m ladok_vakt --all
```

Kör `python3 -m ladok_vakt` utan argument för cron-läget: det skriver bara när
det finns något att säga. Ställ in en notifieringskanal nedan för att få larm.

## Funktioner

- Hämtar dina kurstillfällen, registreringsperioder och kursperioder.
- Skriver en rad per fönster som är öppet nu eller stänger inom det valda antalet dagar.
- Larmar vid fel, till exempel om ingen hämtning lyckats på 36 timmar, kurslistan är tom, sessionen har dött eller svaret är felaktigt.
- Skickar en veckohälsokoll på måndagar.
- Anmäler, avanmäler eller ändrar ej något.
- Valfri inloggningsfunktion för att behålla en aktiv session över tid

## Schemalägg körningen

### cron

Exempel: kör 07:10 varje dag.

```cron
10 7 * * *  cd $HOME/src/ladok-vakt && LADOK_MAIL_TO=du@example.com /usr/bin/python3 -m ladok_vakt
```

### systemd

Exempelfiler finns i [`examples/`](examples/).

```bash
cp examples/ladok-vakt.service examples/ladok-vakt.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ladok-vakt.timer
```

### Agent

lägg körningen i agentens cron eller
registrera [`skill/SKILL.md`](skill/SKILL.md) som en skill. Skriptet är gjort för
att kunna köras utan att behöva tolkas som fritext.

## Välj notifieringskanal

| Kanal | Variabler | Information |
|---|---|---|
| **Resend** (mejl) | `LADOK_MAIL_TO`, `LADOK_MAIL_FROM`, `~/<PATH TO RESEND_KEY>` | Mejlet skickas via `https://api.resend.com/emails`. Fungerar med gratisnivån. |
| **Gmail** (mejl) | `LADOK_MAIL_TO`, `LADOK_MAIL_FROM`, `LADOK_SMTP_USER`, `LADOK_SMTP_PASSWORD_FILE` | Kräver ett [app-lösenord](https://myaccount.google.com/apppasswords) från ett Gmail-konto med 2FA. Sätt `LADOK_SMTP_PASSWORD_FILE=$<PATH TO GMAIL_APP_PASSWORD>`. När variabeln är satt används SMTP i stället för Resend. |
| **Telegram** | `LADOK_TELEGRAM_TOKEN`, `LADOK_TELEGRAM_CHAT` | Bot och chat-id; enkel push till mobilen. |
| **Slack** | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=slack` | Inkommande webhook; skickar `{"text": ...}`. |
| **Discord** | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=discord` | Skickar `{"content": ...}`. |
| **ntfy** | `LADOK_WEBHOOK_URL=https://ntfy.sh/<topic>`, `LADOK_WEBHOOK_FORMAT=ntfy` | Ingen registrering; välj ett topic-namn. |
| **Egen webhook** | `LADOK_WEBHOOK_URL=...`, `LADOK_WEBHOOK_FORMAT=generic` | Skickar `{"text": ..., "source": "ladok-vakt"}`. |
| **Eget kommando** | `LADOK_NOTIFY_COMMAND='curl -d @- https://...'` | Skickas via stdin |

## Valfri automatisk inloggning


Ladok-sessionen kan ta slut när webbläsaren startas om eller efter en tids inaktivitet. Med `LADOK_AUTOLOGIN=1` försöker skriptet logga in igen innan det larmar. Förutsätter att du har inlogg sparat i sessionen som skripten använder. 

```bash
export LADOK_IDP_ENTITY='https://swamid2.shh.se/idp/shibboleth'   # lärosätets SWAMID entityID
export LADOK_AUTOLOGIN=1
python3 -m ladok_vakt --login   # prova i förväg
```

Skriptet öppnar inloggningen via SWAMID, väntar tills Chrome har fyllt i
formuläret och klickar på Login med ett riktigt musklick. Därefter kontrollerar
det att Ladok svarar innan inloggningen rapporteras som lyckad.

**Säkerhetsgränser:** skriptet läser aldrig lösenordsfältets värde, sparar inget,
skriver inget, skickar inget och använder inte webbläsarens lösenordshanterar-API.
Det kontrollerar bara, har fälten fyllts i?, och klickar. Se koden i
`ladok_vakt/login.py`; testet i `tests/` larmar om någon lägger till avläsning av
fältvärden.

För att omloggningen ska fungera måste lösenordet vara sparat i den Chrome-profil
skriptet använder. Inloggningen får inte kräva 2FA, engångskod eller godkännande i
en app. Om den gör det misslyckas omloggningen och du får ett fellarm — skriptet
gissar inte.

## Konfiguration

| Variabel | Standard | Betydelse |
|---|---|---|
| `LADOK_CDP` | `http://127.0.0.1:9233` | Chrome DevTools-endpoint för din inloggade session |
| `LADOK_LAROSATE` | `38` | Lärosätets id i Ladok (38 = Sophiahemmet) |
| `LADOK_MAIL_TO` | tom | Mottagare; tomt värde stänger av mejl |
| `LADOK_MAIL_FROM` | `ladok-vakt <vakt@example.org>` | Avsändare; måste vara verifierad hos Resend |
| `LADOK_CLOSE_DAYS` | `3` | Larma när fönstret stänger inom så många dagar |
| `LADOK_AUTOLOGIN` | `0` | Sätt till `1` för att försöka logga in igen med sparat lösenord |
| `LADOK_IDP_ENTITY` | tom | Lärosätets SWAMID entityID; krävs för omloggning |
| `LADOK_STALE_HOURS` | `36` | Larma när senaste lyckade hämtning är äldre än så här |
| `LADOK_MAIL_MIN_HOURS` | `20` | Minsta tid mellan fellarm per kanal |
| `LADOK_HEARTBEAT` | `1` | Veckohälsokoll på måndagar; `0` stänger av |
| `LADOK_STATE_DIR` | `~/.local/state/ladok-vakt` | Katalog för status och logg |

Status sparas i `$LADOK_STATE_DIR/state.json`, och historik i `ladok-vakt.log`.

## Så fungerar det

Ladok har inget student-API med token. Studentwebben hämtar data via
`https://student.ladok.se/student/proxy/<lärosäte>/...` med sessionskakan.
`ladok-vakt` gör motsvarande anrop från en flik på samma origin via Chrome DevTools-protokollet.

| Data | Endpoint |
|---|---|
| Inloggad student | `/studiedeltagande/internal/student/inloggad/som` |
| Kurstillfällen och registreringsperiod | `/studiedeltagande/internal/tillfallesdeltagande/kurstillfallesdeltagande/student/<uid>` |
| Tentamensanmälningar | `/resultat/internal/studentweb/aktivitetstillfalle/inloggadstudent/relevanta` |

Svaren är XML i Ladoks schema. Skriptet använder bland annat
`sd:Registreringsperiod` (`base:Startdatum` / `base:Slutdatum`), `sd:Tillstand`
och `sd:UtbildningsPeriodFran/Till`.

## Integritet

Ingen data lämnar datorn utom själva larmet. Sessionskakan stannar i din
Chrome-profil. Skriptet skickar bara kursnamn, datum och varningstext till den
kanal du väljer. Ingen statistik, telemetri eller något konto.

Lösenordet lämnar aldrig webbläsaren: skriptet läser, sparar, skickar eller loggar
inte lösenordet. Det använder inloggningen du själv har sparat i Chrome. Automatisk inloggning är avstängd som standard; om du väljer att aktivera den ansvarar du
själv för att det följer ditt lärosätes regler.

## Licens

MIT

---

<sub>

*Xom är intresserad. Peka din agent på repot och se vad som händer.*

</sub>
