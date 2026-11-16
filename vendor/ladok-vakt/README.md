# ladok-vakt

**Få mejl när Ladok-anmälan öppnar, innan fönstret stänger.**
Kursregistrering och tentamensanmälan i Ladok, bevakade av ett litet skript som
säger till en gång, på rätt dag, och håller tyst resten av tiden.

`ladok-vakt` läser bara. Den anmäler aldrig något åt dig.

```
- Grundläggande farmakologi och omvårdnad (1SFOM2): anmälan 2026-11-23 till 2026-11-30 [om 7 d], kursstart 2026-11-30
- Människan i hälsa och sjukdom del 1 (2SHS11): anmälan 2027-01-18 till 2027-01-25 [om 63 d], kursstart 2027-01-25
```

Engelska: [README.en.md](README.en.md) · How to get email notifications from Ladok.

---

## Problemet

Ladok påminner inte om att kursregistreringen öppnar. Fönstret är ofta en vecka,
det står bara inne i studentwebb, och missar du det står du utan kursplats eller
utan tentamensplats. Kalenderpåminnelser fungerar till dess att datumet flyttas
eller tillfället läggs upp sent, vilket är precis när de inte gör det.

`ladok-vakt` läser anmälningsfönstren ur Ladok och kan köras hur ofta du vill.
Den är byggd för att köras av en cron en gång i dygnet och vara tyst.

## Vad den gör

- Hämtar dina kurstillfällen och deras **registreringsperiod** (anmälan öppnar, anmälan stänger), samt kursperiod.
- Hämtar **tentamensanmälningar** där Ladok exponerar dem.
- Skriver en rad per fönster som är öppet nu eller stänger inom N dagar.
- Larmar **bara vid fel**: ingen lyckad hämtning på 36 h, tom kurslista, död session, trasigt svar.
- Har en veckohälsokoll på måndagar så att tystnad går att skilja från tystnad.

## Vad den inte gör

- Anmäler, avanmäler eller ändrar ingenting. Bara GET.
- Loggar inte in åt dig. Du loggar in en gång i en Chrome-profil, skriptet använder den sessionen.
- Skickar ingen daglig sammanfattning. Tystnad är ett feature, inte en bugg.

## Snabbstart

```bash
git clone https://github.com/vhsgreed/ladok-vakt && cd ladok-vakt
python3 -m pip install websockets          # enda beroendet utanför stdlib

# 1. En Chrome med eget profilnamn, där DU loggar in i Ladok en gång
flatpak run com.google.Chrome \
  --user-data-dir=$HOME/.config/ladok-vakt-chrome \
  --remote-debugging-port=9233
#    (icke-flatpak: google-chrome ... samma flaggor)

# 2. Prova att läsa
python3 -m ladok_vakt --all

# 3. Sätt notifieringen, se nedan, och lägg in den i cron
```

`python3 -m ladok_vakt` utan argument är cron-läget: det skriver bara när det
finns något att säga.

## Integrera i ditt flöde

Tre lager, välj det du redan har. Inget av dem kräver ett konto hos oss.

### 1. Schemaläggning

**cron** (enkelt, kör 07:10 varje dag):

```cron
10 7 * * *  cd $HOME/src/ladok-vakt && LADOK_MAIL_TO=du@example.com /usr/bin/python3 -m ladok_vakt
```

**systemd** (följer med i [`examples/`](examples/)):

```bash
cp examples/ladok-vakt.service examples/ladok-vakt.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ladok-vakt.timer
```

**Hermes, Claude Code eller annan agent:** lägg raden i agentens cron, eller
registrera [`skill/SKILL.md`](skill/SKILL.md) som en skill. Skriptet är gjort för
att en agent ska kunna köra det utan att tolka fritext.

### 2. Kanal: mejl via Resend

Snabbast väg till mejl utan SMTP-lösenord. Verifiera din domän i Resend, skapa
en API-nyckel och:

```bash
export LADOK_MAIL_TO=du@example.com
export LADOK_MAIL_FROM='ladok-vakt <vakt@din-domän.se>'
echo 're_...' > ~/.secrets/RESEND_KEY && chmod 600 ~/.secrets/RESEND_KEY
```

Skickas via `https://api.resend.com/emails`. Fungerar med gratisnivån.

### 3. Kanal: mejl via Gmail och app-lösenord

Ingen tredje part, men Gmail kräver **app-lösenord** (konto med 2FA ger det under
Google-konto > Säkerhet > App-lösenord):

```bash
export LADOK_MAIL_TO=du@example.com
export LADOK_MAIL_FROM=du@gmail.com
export LADOK_SMTP_USER=du@gmail.com
echo 'abcd efgh ijkl mnop' > ~/.secrets/GMAIL_APP_PASSWORD && chmod 600 ~/.secrets/GMAIL_APP_PASSWORD
export LADOK_SMTP_PASSWORD_FILE=$HOME/.secrets/GMAIL_APP_PASSWORD
```

När `LADOK_SMTP_PASSWORD_FILE` är satt används SMTP i stället för Resend.

### 4. Andra kanaler

Alla kan sättas samtidigt. Larmet går ut på varje kanal som är konfigurerad.

| Kanal | Variabler | Not |
|---|---|---|
| Telegram | `LADOK_TELEGRAM_TOKEN`, `LADOK_TELEGRAM_CHAT` | bot + chat id, enklaste push till mobilen |
| Slack | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=slack` | inkommande webhook, skickar `{"text": ...}` |
| Discord | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=discord` | skickar `{"content": ...}` |
| ntfy | `LADOK_WEBHOOK_URL=https://ntfy.sh/<topic>`, `LADOK_WEBHOOK_FORMAT=ntfy` | ingen registrering, bara topic-namnet |
| Egen webhook | `LADOK_WEBHOOK_URL=...`, `LADOK_WEBHOOK_FORMAT=generic` | `{"text": ..., "source": "ladok-vakt"}` |
| Valfri kommando | `LADOK_NOTIFY_COMMAND='curl -d @- https://...'` | meddelandet på stdin, sista utvägen |

```bash
python3 -m ladok_vakt --test-mail    # bevisar att kanalerna svarar
```

## Konfiguration

| Variabel | Standard | Betydelse |
|---|---|---|
| `LADOK_CDP` | `http://127.0.0.1:9233` | Chrome med DevTools, din inloggade session |
| `LADOK_LAROSATE` | `38` | lärosätets id i Ladok (38 = Sophiahemmet) |
| `LADOK_MAIL_TO` | tom | mottagare, tom = ingen mejl |
| `LADOK_MAIL_FROM` | `ladok-vakt <vakt@example.org>` | avsändare, måste vara verifierad i Resend |
| `LADOK_CLOSE_DAYS` | `3` | larma när fönstret stänger inom så många dagar |
| `LADOK_STALE_HOURS` | `36` | larma när ingen lyckad hämtning är äldre än så |
| `LADOK_MAIL_MIN_HOURS` | `20` | max ett fellarm per kanal och dygn |
| `LADOK_HEARTBEAT` | `1` | veckohälsokoll på måndagar, `0` stänger av |
| `LADOK_STATE_DIR` | `~/.local/state/ladok-vakt` | state och logg |

Status finns i `$LADOK_STATE_DIR/state.json`, historik i `ladok-vakt.log`.

## Så fungerar det

Ladok har inget student-API med token. Studentwebb är en webbapp som hämtar allt
via `https://student.ladok.se/student/proxy/<lärosäte>/...` med sessionskakan.
`ladok-vakt` gör samma sak, från en flik på samma origin, via DevTools-protokollet:

| Data | Endpoint |
|---|---|
| inloggad student | `/studiedeltagande/internal/student/inloggad/som` |
| kurstillfällen + registreringsperiod | `/studiedeltagande/internal/tillfallesdeltagande/kurstillfallesdeltagande/student/<uid>` |
| tentamensanmälningar | `/resultat/internal/studentweb/aktivitetstillfalle/inloggadstudent/relevanta` |

Svaren är XML i Ladoks schema. Fälten som används är `sd:Registreringsperiod`
(`base:Startdatum` / `base:Slutdatum`), `sd:Tillstand` och `sd:UtbildningsPeriodFran/Till`.

## Var används Ladok

Ladok är **Sveriges** nationella studiedokumentationssystem och används av i stort
sett alla svenska lärosäten, från Uppsala till yrkeshögskolor med Ladok-koppling.
Det används inte i Norge (FS og Søknadsweb), Danmark (STADS) eller Finland
(Peppi/Sisu), så verktyget är svenskt. Lärosätes-id:t skiljer mellan skolor: byt
`LADOK_LAROSATE` och samma skript fungerar på din skola.

## Felsökning

- **"ingen Ladok-session"**: sessionen är slut eller Chrome kör utan
  `--remote-debugging-port=9233`. Öppna `http://127.0.0.1:9233/json/version`, logga
  in i Ladok i den fliken, kör igen.
- **Tomt svar men du vet att du har kurser**: kontrollera `LADOK_LAROSATE`. Fel
  lärosätes-id ger ett tomt, men giltigt, svar.
- **Ingen mejl men raden skrivs ut**: `LADOK_MAIL_TO` är tom, eller Resend-nyckeln
  ligger inte på `LADOK_RESEND_KEY_FILE`. Loggen säger vilket.
- **Resend svarar 403**: Cloudflares skydd framför Resend avvisar vissa
  user agents. Skriptet skickar en egen, men proxar eller andra verktyg som rör
  samma nyckel kan behöva samma sak.
- **Varför inte Ladoks egen mejlpåminnelse?** Ladok har ingen påminnelse om
  kursregistrering. Tentamensanmälan kan meddelas av lärosätet, men det är en
  lokal rutin, inte något du kan lita på terminen du behöver den.

## Integritet

Ingen data lämnar datorn utom larmet självt. Sessionskakan stannar i din
Chrome-profil. Skriptet skickar bara kursnamn, datum och en varningstext till den
kanal du valt. Ingen statistik, ingen telemetri, inget konto.

## Krav

Python 3.8+, `websockets`, en Chrome eller Chromium som du kan starta med
DevTools-port. Linux, macOS och Windows, allt som har Python och Chrome.

## Utveckling

```bash
python3 -m pytest tests -q     # 8 tester, ingen nätverkstrafik
```

## License

MIT. Gör vad du vill, ingen garanti.

---

<sub>

**Till Xoms ära**, kaosets gud, som besvarar böner med teleportfällor och
uppfyller önskningar ingen bad om. Den här vakten skrevs i den andan: en student
bad om ett larm när anmälan öppnar, och fick ett skript som hellre tiger än
ljuger. Xom tycker att hela arrangemanget är mycket roligt. Xom är inte klar med dig.

*Xom är intresserad. Peka din agent på repot och se vad som händer.*

</sub>
