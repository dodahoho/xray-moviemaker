# Xray Moviemaker

Skapar videofiler (MP4) från DvXray-datasetet (behöver laddas ned separat) för objektdetektion. Scriptet blandar
positiva samples (förbjudna föremål finns) och negativa samples (inga förbjudna
föremål).

Som standard skapas **två videofiler med samma samples och ordning**: en med vy
uppifrån (`top`) och en med vy från sidan (`side`). Det liknar en live-röntgen med
dual view. Bilderna rullar kontinuerligt som på ett löpande band, 15 samples visas
i 5 sekunder vardera. Bakgrunden mellan bilderna är vit som standard.

Det går också att visa båda vyerna sida vid sida i en enda video, eller bara en av
vyerna. Allt styrs med kommandoradsflaggor.

## Krav

- Ladda hem DVXray dataset separat [Positive Samples](https://drive.google.com/file/d/1NK1DWLMztROwRkJIlYAnexWLgyFv_gDF/view?usp=drive_link)  [Negative Samples](https://drive.google.com/file/d/18QJyRNVDG6jguNmV04GRuZM98IGdizUb/view?usp=drive_link) och packa upp i arbetskatalogen
- [uv](https://docs.astral.sh/uv/) (rekommenderat) – installerar Python-beroendena
  automatiskt.
- Inget systeminstall av ffmpeg krävs: `imageio-ffmpeg` innehåller en portabel
  ffmpeg-binary som används automatiskt.

Om uv saknas:

```powershell
winget install --id=astral-sh.uv -e
```

Alternativt utan uv:

```powershell
pip install pillow imageio-ffmpeg
python make_video.py --help
```

## Snabbstart

```powershell
# Standard: två filer, 15 samples, 5 s per bild, kontinuerligt löpande band
uv run make_video.py
```

Det skapar:

```
xray_slideshow_top.mp4    vy uppifrån
xray_slideshow_side.mp4   vy från sidan
```

Båda filerna innehåller exakt samma samples i samma ordning, så de kan visas
parallellt som en dual-view-röntgen.

```powershell
# Båda vyerna sida vid sida i en enda video
uv run make_video.py --view both -o slideshow.mp4

# Endast vy uppifrån respektive endast från sidan
uv run make_video.py --view top -o overhead.mp4
uv run make_video.py --view side -o side.mp4

# Med klass, objektetiketter och bounding boxes
uv run make_video.py -n 30 -s 2 --overlay all -o labels.mp4

# Visa datasetstatistik utan att rendera
uv run make_video.py --stats
```

Första körningen laddar ner Pillow och imageio-ffmpeg (ca 40 MB) till uv-cachen.
Därefter går det snabbt.

## Färg, komprimering och videoformat

Standard är **H.264 (`libx264`) med 4:4:4** (`yuv444p`, i praktiken
`yuvj444p` när fullt färgomfång används), alltså ingen chroma-subsampling, fullt
färgomfång (`pc`) och CRF 18. Det undviker den färgförskjutning som 4:2:0/4:2:2 kan
ge.

För att styra detta finns:

| Flagga | Beskrivning |
| --- | --- |
| `--codec` | `libx264` (standard), `libx264rgb`, `libx265`, `ffv1`, m.fl. |
| `--pix-fmt` | Pixelfomat. Standard väljs automatiskt: `yuv444p`, `rgb24` för `libx264rgb`, `bgr0` för `ffv1`. |
| `--crf` | Kvalitet för x264/x265. Lägre = bättre, `0` = förlustfri. Standard `18`. |
| `--lossless` | Förlustfri kodning. CRF 0 för x264/x265; FFV1 är alltid förlustfri. |
| `--format` | Tvinga container, t.ex. `mp4` eller `matroska`. Annars styrs det av filändelsen. |
| `--color-range` | `pc` (fullt omfång, standard), `tv` (begränsat) eller `auto`. |
| `--bitrate` | Bitrate för codecs som inte använder CRF. |

### Rekommendationer

```powershell
# Standard: två filer (top/side), 4:4:4, bra balans mellan kvalitet och filstorlek
uv run make_video.py

# En enda video med båda vyerna sida vid sida
uv run make_video.py --view both -o slideshow.mp4

# Exakt RGB och förlustfritt (större fil, men ingen färgkonvertering)
uv run make_video.py --lossless --codec libx264rgb -o exact_rgb.mkv

# FFV1: förlustfritt arkivformat för analys, ännu större filer
uv run make_video.py --codec ffv1 -o archive.mkv

# Bred spelarkompatibilitet, men 4:2:0 och sämre färgnoggrannhet
uv run make_video.py --pix-fmt yuv420p -o compatible.mp4
```

Om detektionsmodellen ska matcha videobilder mot PNG-bilderna är
`libx264rgb` med `--lossless`, eller `ffv1`, säkrast. `yuv444p` undviker
chroma-subsampling men är fortfarande en komprimerad YUV-video. Väljer du
`yuv420p`/`yuv422p` skriver scriptet en varning.

H.264 med 4:4:4 eller RGB kan sakna stöd i vissa webbläsare och hårdvaruacceleration.
För uppspelning i vanliga spelare kan `--pix-fmt yuv420p` behövas, men det är alltså
en avvägning mot färgnoggrannheten.

## Viktiga parametrar

| Flagga | Standard | Beskrivning |
| --- | --- | --- |
| `-n`, `--num-images` | `15` | Antal samples/bildrutor totalt. `0` = alla tillgängliga. |
| `-s`, `--seconds-per-image` | `5.0` | Sekunder per bildruta. Övergången räknas in i tiden. |
| `--view` | `separate` | `separate` = två filer (`top` och `side`) med samma samples, `top` = uppifrån, `side` = från sidan, `both` = båda sida vid sida i en fil. `ol`/`sd` är alias för `top`/`side`. |
| `--transition` | `slide` | `cut` = rakt byte, `slide` = kontinuerligt löpande band, `push` = kort övergång, `fade` = kort toning. |
| `--slide-direction` | `left` | Riktning som nästa bild rullar in från: `left`, `right`, `up`, `down`. |
| `--transition-duration` | `0.6` | Övergångens längd i sekunder (endast `push`/`fade`). |
| `--ease` | `smooth` | `linear` eller `smooth` för `push`/`fade`. `slide` är alltid linjär. |
| `--conveyor-loop` | på | Låt löpande bandet fortsätta med första bilden efter sista. Använd `--no-conveyor-loop` för att stanna på en tom bakgrund. |
| `--positive-ratio` | `0.5` | Andel positiva samples, `0.0`–`1.0`. |
| `--order` | `random` | `random`, `alternating` (varvar pos/neg) eller `sequential`. |
| `--seed` | `1234` | Slumpfrö för reproducerbart urval. `-1` = slumpmässigt. |
| `--allow-repeat` | av | Tillåt upprepade samples om `-n` är fler än datat. |
| `--overlay` | `none` | `none`, `class` (POSITIVE/NEGATIVE), `boxes` (objekt + bb), `all`. |
| `--show-name` | av | Visa samplens namn i klasstexten. |
| `--show-view-labels` | av | Visa `OL (uppifrån)` / `SD (från sidan)` i bilden. |
| `--width`, `--height` | `1280`, `720` | Videoupplösning. Måste vara jämna tal. |
| `--fps` | `30` | Bildfrekvens. |
| `--margin`, `--gap` | `24`, `24` | Marginal runt bildytan och mellanrum mellan vyerna. |
| `--background` | `#ffffff` | Bakgrundsfärg bakom bilder och i mellanrum. Använd `#101010` för mörk bakgrund. |
| `--font`, `--font-size` | auto, `28` | Typsnitt och storlek för overlay. |
| `--codec` | `libx264` | Videokodek. Använd `libx264rgb` eller `ffv1` för exakt RGB. |
| `--crf`, `--preset` | `18`, `medium` | Kvalitet/inställning för `libx264`/`libx265`. |
| `--lossless` | av | Förlustfri kodning (CRF 0 för x264/x265). |
| `--pix-fmt` | auto | `yuv444p`, `rgb24` för `libx264rgb`, `bgr0` för `ffv1`. |
| `--format` | auto | Tvinga container, t.ex. `mp4` eller `matroska`. |
| `--color-range` | `pc` | Fullt färgomfång. `tv` ger begränsat omfång. |
| `--ffmpeg` | auto | Egen sökväg till `ffmpeg.exe`. |
| `--dry-run` | av | Visa urval och ffmpeg-kommando utan att rendera. |
| `--no-progress` | av | Stäng av progressutskriften. |

Alla flaggor och standardvärden:

```powershell
uv run make_video.py --help
```

## Så fungerar urvalet

- Positiva samples läses från `DvXray_Positive_Samples` (`P*.png` + `P*.json`).
- Negativa samples läses från `DvXray_Negative_Samples` (`N*.png` + `N*.json`).
- Ett sample kräver att de valda vyerna finns. Med standardvärdet `--view separate`
  (och med `--view both`) krävs både `_OL.png` och `_SD.png`.
- `--positive-ratio 0.5` ger ungefär hälften positiva och hälften negativa. Om en
  klass inte räcker till fylls resten från den andra klassen.
- `--order alternating` varvar positiva och negativa, vilket är praktiskt för att
  snabbt se skillnaden i en video.
- `--seed` gör urvalet reproducerbart. Samma seed och samma parametrar ger samma
  video.
- Vid `--view separate` görs urvalet **en gång** och används för båda filerna, så
  `top` och `side` visar exakt samma samples i samma ordning.

Total videolängd är `antal bilder × seconds-per-image`. För `push`/`fade` sker
övergången i slutet av varje bilds tidslucka och ingår i den tiden. För `slide`
rör sig bilderna kontinuerligt under hela videon – det finns ingen stillastående
paus mellan bilderna.

### Kontinuerligt löpande band

`--transition slide` innebär att bilden rör sig under hela sin tidslucka: nästa
bild rullar in från vald riktning samtidigt som föregående rullar ut. Det är alltså
inte en kort övergång följd av en stillastående bild.

```powershell
# Kontinuerligt band, nästa bild rullar in från vänster
uv run make_video.py -n 40 -s 2 --transition slide -o conveyor.mp4

# Samma sak men bandet stannar efter sista bilden
uv run make_video.py -n 40 -s 2 --transition slide --no-conveyor-loop -o conveyor_end.mp4

# Gammal stil: kort push-overgång och sedan stillastående bild
uv run make_video.py -n 40 -s 2 --transition push --transition-duration 0.6 -o push.mp4
```

Som standard loopar bandet: efter sista bilden rullar den första in igen, vilket
gör att videon kan spelas upp i loop utan tydligt hack.

## Vyer, filnamn och overlay

`OL` antas vara vy uppifrån (`top`) och `SD` vy från sidan (`side`). Om datasetet
skulle vara mappat tvärtom ändrar du `VIEW_SUFFIX`/`VIEW_LABEL` högst upp i
`make_video.py`.

Standardläget `--view separate` skapar två filer med samma samples och ordning. Vyn
läggs automatiskt till i filnamnet:

```
-o xray_slideshow.mp4   ->   xray_slideshow_top.mp4
                             xray_slideshow_side.mp4
```

Om basnamnet redan slutar på den vy som ska skapas läggs inget extra suffix till
(t.ex. behåller `-o video_top.mp4` namnet för top-filen). Med `--view both` skapas
en enda fil med suffixet `_both`.

```powershell
# Standard: två filer, top och side
uv run make_video.py

# Endast en vy
uv run make_video.py --view top -o overhead.mp4
uv run make_video.py --view side -o side.mp4

# Båda vyerna sida vid sida i samma fil
uv run make_video.py --view both -o combined.mp4
```

Som standard visas inga etiketter eller bounding boxes. Slå på det med:

```powershell
# Endast POSITIVE/NEGATIVE
uv run make_video.py -n 20 --overlay class

# Objektetiketter och bounding boxes (endast positiva samples har objekt)
uv run make_video.py -n 20 --overlay boxes

# Både klass och boxar, plus vynamn och samplens namn
uv run make_video.py -n 20 --overlay all --show-view-labels --show-name
```

Bounding boxes ritas från `ol_bb`/`sd_bb` i JSON-filerna och skalas automatiskt när
bilden passas in i videorutan.

## Exempel på vanliga varianter

```powershell
# Löpande band åt andra hållet (nästa bild glider in från höger)
uv run make_video.py --slide-direction right -o right.mp4

# Endast negativa samples, båda vyerna sida vid sida
uv run make_video.py -n 50 --positive-ratio 0 --view both -o negatives.mp4

# Endast positiva samples, sekventiell ordning
uv run make_video.py -n 50 --positive-ratio 1 --order sequential -o positives.mp4

# 30 % positiva, 70 % negativa, toning
uv run make_video.py -n 100 -s 1 --positive-ratio 0.3 --transition fade -o mixed.mp4

# Extra hög kvalitet och lägre fps för snabbare rendering
uv run make_video.py -n 100 -s 1.5 --fps 24 --crf 12 --preset slow -o high_quality.mp4

# Kontrollera urval och kommandon utan att rendera
uv run make_video.py --dry-run
```

## Felsökning

- **`ffmpeg hittades inte`** – kör via `uv run`, eller peka ut en binary med
  `--ffmpeg C:\sökväg\ffmpeg.exe`.
- **`hittade inga kompletta samples`** – kontrollera att `_OL.png` och `_SD.png`
  finns med samma basnamn, eller använd `--view top`/`--view side`.
- **`--num-images ... är fler än tillgängliga`** – sänk `-n` eller lägg till
  `--allow-repeat`.
- **`--width och --height måste vara jämna tal`** – använd t.ex. `1280x720` eller
  `1920x1080`.
- **Varning om chroma-subsampling** – du har valt t.ex. `--pix-fmt yuv420p`. Använd
  standardvärdet `yuv444p`, eller `--lossless --codec libx264rgb`/`--codec ffv1` för
  bäst färgnoggrannhet.
- **`FFV1 kan inte skrivas till .mp4`** – använd `-o video.mkv` eller
  `--format matroska`.
- **Långsam rendering** – sänk `--fps`, använd `--preset fast` eller mindre
  `--width`/`--height`. Bildkompositionen sker i Python och skickas som råa
  bildrutor till ffmpeg.

## Filer

- `make_video.py` – hela scriptet med PEP 723-metadata för uv.
- `DvXray_Positive_Samples/` – positiva samples.
- `DvXray_Negative_Samples/` – negativa samples.
