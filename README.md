# Sony Popup

Popup w stylu iOS dla słuchawek **Sony WH-1000XM6** i **WH-ULT900N** na Windows — odpowiednik
[AirPodsDesktop](https://github.com/SpriteOvO/AirPodsDesktop) dla Sony.

## Funkcje

- popup z poziomem baterii po podłączeniu słuchawek
- przełączanie trybu: **redukcja szumów / otoczenie (z poziomem) / wyłączone**, synchronizowane z przyciskiem na słuchawkach
- sterowanie odtwarzaniem (poprzedni / play-pauza / następny) + tytuł aktualnego utworu
- ikona w trayu z kolorową kropką baterii
- globalny skrót `Ctrl+Alt+Shift+H` (pokaż/schowaj) — działa np. z Action Ring w Logi Options+
- autostart z systemem, jedna instancja (ponowne uruchomienie = pokaż popup)

## Instalacja

**Gotowy build:** pobierz `SonyPopup-win-x64.zip` z [Releases](../../releases) albo z artefaktów
GitHub Actions, rozpakuj i uruchom `SonyPopup.exe`.

**Ze źródeł** (Python 3.12 dla Windows):

```powershell
git clone https://github.com/cristianwolff33/sony-popup
cd sony-popup
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Skrypt buduje exe (PyInstaller), instaluje do `%LOCALAPPDATA%\Programs\SonyPopup`,
dodaje skrót w menu Start i autostart.

**Bez budowania:**

```powershell
pip install -r requirements.txt
pythonw sony_popup.py
```

### Zdjęcie słuchawek

`wh-1000xm6.png`, `wh-ult900n.png` — zdjęcia produktowe Sony (repo prywatne, tylko na własny użytek).
Nazwa pliku = klucz modelu z listy `DEVICES` w `sony_popup.py`. Nowy model: dopisz go do `DEVICES`
i wrzuć `<klucz>.png`.

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
