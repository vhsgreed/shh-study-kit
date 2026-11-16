# Hur får jag mejl när Ladok-anmälan öppnar?

Kort svar: Ladok skickar inget sådant mejl själv, så du måste bevaka fönstret.
`ladok-vakt` gör det och mejlar dig när anmälan öppnar eller när den håller på att
stänga.

## Steg för steg

1. **Hitta ditt lärosätes-id.** Öppna studentwebb och titta på en endpoint i
   utvecklarverktygen: adressen innehåller `/student/proxy/<id>/`. Sätt
   `LADOK_LAROSATE=<id>`.
2. **Logga in en gång** i en Chrome som startats med
   `--remote-debugging-port=9233` och en egen `--user-data-dir`.
3. **Välj kanal.** Resend om du vill ha mejl utan SMTP-lösenord, Gmail med
   app-lösenord om du inte vill ha en tredje part, annars Telegram, Slack, Discord
   eller ntfy.
4. **Schemalägg.** En cron-rad eller en systemd-timer, en gång i dygnet räcker.
5. **Testa.** `python3 -m ladok_vakt --test-mail` och `--all`.

## Vad du får

Ett meddelande per fönster som är öppet nu eller stänger inom tre dagar. Inget
annat. Går hämtningen sönder får du ett larm i stället, för tystnad ska betyda
"allt är bra", inte "skriptet har varit dött i en vecka".

## Vanliga frågor

**Påminner Ladok inte själv?** Inte om kursregistrering. Tentamensanmälan kan
aviseras av lärosätet, men det är en lokal rutin.

**Fungerar det i Norge, Danmark eller Finland?** Nej. Ladok är svenskt. Norge har
FS og Søknadsweb, Danmark STADS, Finland Peppi/Sisu.

**Är det säkert?** Skriptet gör bara GET-anrop med din egen session och skickar
bara kursnamn och datum till den kanal du valt. Ingen inloggning automatas.
