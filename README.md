# Sony Popup

Popup w stylu iOS dla słuchawek Sony na Windows — odpowiednik
[AirPodsDesktop](https://github.com/SpriteOvO/AirPodsDesktop) dla Sony.

## Obsługiwane modele

| Model | Popup, bateria, odtwarzacz | Sterowanie ANC / otoczenie | Protokół |
|---|---|---|---|
| WH-1000XM6 | ✅ | ✅ (sprawdzone) | MDR v2 |
| WH-1000XM5 | ✅ | ✅ (wg SonyHeadphonesClient) | MDR v2 |
| WH-ULT900N | ✅ | niesprawdzone | MDR v2 |
| WH-1000XM4 | ✅ | niesprawdzone | MDR v1 |

Nowy model: dopisz go do `DEVICES` w `sony_popup.py` (nazwa Bluetooth, protokół) i wrzuć zdjęcie `<klucz>.png`.

## Funkcje

- popup z poziomem baterii po podłączeniu słuchawek
- przełączanie trybu: **redukcja szumów / otoczenie (z poziomem) / wyłączone**, synchronizowane z przyciskiem na słuchawkach
- **equalizer**: presety (Bass Boost, Vocal, Heavy…) + suwak **Clear Bass** −10…+10, także w menu traya
- sterowanie odtwarzaniem (poprzedni / play-pauza / następny) + tytuł aktualnego utworu
- ikona w trayu z kolorową kropką baterii
- globalny skrót `Ctrl+Alt+Shift+H` (pokaż/schowaj) — działa np. z Action Ring w Logi Options+
- autostart z systemem, jedna instancja (ponowne uruchomienie = pokaż popup)

## Instalacja

**Instalator (zalecane):** pobierz `SonyPopup-Setup.exe` z [Releases](../../releases), uruchom
i przeklikaj kreator. Nie wymaga uprawnień administratora ani Pythona. Tworzy skrót w menu Start,
opcjonalnie autostart i ikonę na pulpicie; odinstalowanie przez *Ustawienia → Aplikacje*.

Windows SmartScreen może ostrzec, bo instalator nie jest podpisany — *Więcej informacji → Uruchom mimo to*.

**Wersja przenośna:** `SonyPopup-win-x64-portable.zip` — rozpakuj i uruchom `SonyPopup.exe`.

**Nowe wydanie** (buduje instalator na GitHub Actions i publikuje w Releases):

```powershell
git tag v1.1.0
git push origin v1.1.0
```

Każdy push na `main` też buduje instalator — do pobrania w *Actions → ostatnie uruchomienie → Artifacts*.

**Ze źródeł** (Python 3.12 dla Windows):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### Zdjęcie słuchawek

`wh-1000xm6.png`, `wh-ult900n.png` — zdjęcia produktowe Sony (repo prywatne, tylko na własny użytek).
Nazwa pliku = klucz modelu z listy `DEVICES` w `sony_popup.py`. Nowy model: dopisz go do `DEVICES`
i wrzuć `<klucz>.png`.

## macOS (MacBook z notchem)

Natywna aplikacja w Swifcie (`macos/`). Najedź kursorem na notch — rozwija się z niego wyspa
w stylu Dynamic Island: zdjęcie i bateria słuchawek, tryb ANC / otoczenie, odtwarzacz.
Po połączeniu słuchawek wyspa pokazuje się sama. Na Macach bez notcha działa środek paska menu.
W pasku menu jest ikona słuchawek z baterią (tryb dźwięku, autostart, log).

**Instalacja:** pobierz `SonyPopup-macOS.dmg` z [Releases](../../releases), przeciągnij
*Sony Popup* do *Aplikacji*. Aplikacja nie jest podpisana przez Apple, więc przy pierwszym
uruchomieniu:

```bash
xattr -dr com.apple.quarantine "/Applications/Sony Popup.app"
```

albo *Ustawienia systemowe → Prywatność i ochrona → Otwórz mimo to*.

Uprawnienia, o które zapyta: **Bluetooth** (wymagane), **Automatyzacja** Spotify/Muzyka (tytuł utworu),
**Dostępność** (klawisze multimedialne dla innych odtwarzaczy, np. YouTube).

**Ze źródeł** (Xcode albo `xcode-select --install`):

```bash
./macos/build_app.sh        # → macos/build/Sony Popup.app + SonyPopup-macOS.dmg
```

Bateria i tryby na macOS idą przez protokół Sony MDR (RFCOMM), więc bez połączenia sterującego
nie będzie też poziomu baterii. Log: `~/Library/Logs/SonyPopup.log`.

## Jak to działa

| Element | Źródło |
|---|---|
| Wykrycie połączenia | WinRT `BluetoothDevice.ConnectionStatusChanged` |
| Bateria | właściwość PnP `{104EA319-…} 2` (profil HFP) |
| ANC / otoczenie | protokół Sony MDR v2 po RFCOMM (`sony_mdr.py`) |
| Odtwarzacz | wirtualne klawisze multimedialne + `GlobalSystemMediaTransportControlsSessionManager` |

Log: `sony_popup.log` obok exe/skryptu.

## Podziękowania

Format pakietów i komendy NC/ASM protokołu Sony MDR odtworzone na podstawie
[mos9527/SonyHeadphonesClient](https://github.com/mos9527/SonyHeadphonesClient) (MIT).

Projekt niezależny, niezwiązany z Sony. „Sony” i „WH-1000XM6” są znakami towarowymi Sony Group Corporation.

## Licencja

MIT
