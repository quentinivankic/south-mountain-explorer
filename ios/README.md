# TrekDex – iOS App

SwiftUI app targeting iOS 26 with native Liquid Glass UI.

## First-time setup on your cloud Mac

### 1. Install XcodeGen
```bash
brew install xcodegen
```

### 2. Copy the area index
```bash
cp ../public/areas/index.json SouthMountainExplorer/Resources/areas-index.json
```

### 3. Generate the Xcode project
```bash
cd ios
xcodegen generate
```
This creates `SouthMountainExplorer.xcodeproj`. Open it in Xcode.

### 4. Open in Xcode
```bash
open SouthMountainExplorer.xcodeproj
```

### 5. Set your Team
In Xcode → Project → Signing & Capabilities → set your Apple Developer Team.

### 6. Build & Archive for TestFlight
Product → Archive → Distribute App → TestFlight

## Install on a phone over USB
```bash
scripts/install-device.sh --dry-run <device-udid>   # check only
scripts/install-device.sh <device-udid>             # build and install
```
Run from the repo root with the phone plugged in, unlocked and trusted. The
script checks the phone, builds Release with development signing, and
installs over the existing app (it never uninstalls, so data stays). If the
install fails it checks the phone again and retries once. It stops when the
phone is only on Wi-Fi; add `--allow-network` to install anyway. List UDIDs
with `xcrun devicectl list devices`. Settings > About > Build then shows the
commit, for example `1 (291be0047)`.

## Project structure

```
ios/
├── project.yml                          # XcodeGen config
└── SouthMountainExplorer/
    ├── App/
    │   ├── SouthMountainExplorerApp.swift   # Entry point
    │   └── ContentView.swift                # Tab bar root
    ├── Models/
    │   ├── Trail.swift                      # Trail, AreaSummary, Difficulty
    │   ├── Area.swift                       # Area, AreaRow
    │   └── HikeRecording.swift              # Recording types
    ├── Services/
    │   ├── SupabaseService.swift            # Supabase client + AuthService
    │   ├── AreaDataService.swift            # Area index + full area fetching
    │   ├── LocationService.swift            # CoreLocation wrapper
    │   ├── RecordingService.swift           # GPS recording + coverage calc
    │   ├── ProgressService.swift            # Trail completions
    │   ├── CoverageService.swift            # Per-trail coverage (0–1)
    │   └── FavoritesService.swift           # Saved areas
    ├── Views/
    │   ├── Home/
    │   │   ├── HomeView.swift               # Nearby + favourites
    │   │   └── AreaCard.swift               # Horizontal scroll card
    │   ├── Area/
    │   │   ├── AreaView.swift               # Map + trail list + record button
    │   │   ├── TrailMapView.swift           # MapKit polylines
    │   │   ├── TrailListView.swift          # Trail rows with coverage
    │   │   └── RecordingPanel.swift         # Live HUD + summary sheet
    │   ├── Browse/
    │   │   └── BrowseView.swift             # Search across all 17k areas
    │   ├── History/
    │   │   └── HistoryView.swift            # Past hike list
    │   ├── Auth/
    │   │   └── AuthView.swift               # Sign in / sign up
    │   └── Settings/
    │       └── SettingsView.swift           # Account + data reset
    ├── Utilities/
    │   └── Haversine.swift                  # Distance calculations
    └── Resources/
        └── areas-index.json                 # (copy from ../public/areas/index.json)
```

## Liquid Glass

The app targets iOS 26. Liquid Glass is applied:
- **Tab bar** – automatically by iOS 26 `TabView`
- **Navigation bars** – automatically by iOS 26 `NavigationStack`
- **Recording panel** – `.glassEffect(in: .rect(cornerRadius: 24))`
- **Area cards** – `.ultraThinMaterial` + `.glassEffect(in: .circle)` on buttons
- **Auth fields** – `.glassEffect(in: .rect(cornerRadius: 14))`
- **Summary stats** – `.glassEffect(in: .rect(cornerRadius: 16))`
