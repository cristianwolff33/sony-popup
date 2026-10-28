import SwiftUI

/// Kształt wyspy: płaska góra (przylega do krawędzi ekranu), zaokrąglony dół,
/// lekkie "uszy" na górnych rogach jak przy notchu.
struct IslandShape: Shape {
    var radius: CGFloat
    var ear: CGFloat

    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { AnimatablePair(radius, ear) }
        set { radius = newValue.first; ear = newValue.second }
    }

    func path(in r: CGRect) -> Path {
        var p = Path()
        let rad = min(radius, r.height / 2, r.width / 2)
        p.move(to: CGPoint(x: r.minX - ear, y: r.minY))
        p.addQuadCurve(to: CGPoint(x: r.minX, y: r.minY + ear), control: CGPoint(x: r.minX, y: r.minY))
        p.addLine(to: CGPoint(x: r.minX, y: r.maxY - rad))
        p.addQuadCurve(to: CGPoint(x: r.minX + rad, y: r.maxY), control: CGPoint(x: r.minX, y: r.maxY))
        p.addLine(to: CGPoint(x: r.maxX - rad, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: r.maxX, y: r.maxY - rad), control: CGPoint(x: r.maxX, y: r.maxY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.minY + ear))
        p.addQuadCurve(to: CGPoint(x: r.maxX + ear, y: r.minY), control: CGPoint(x: r.maxX, y: r.minY))
        p.closeSubpath()
        return p
    }
}

struct IslandView: View {
    @ObservedObject var model: AppModel
    static let width: CGFloat = 400

    private var contentHeight: CGFloat { model.islandContentHeight }

    var body: some View {
        let notch = model.notchSize
        let w = model.expanded ? Self.width : notch.width
        let h = model.expanded ? notch.height + contentHeight : notch.height

        ZStack(alignment: .top) {
            IslandShape(radius: model.expanded ? 30 : 10, ear: model.expanded ? 14 : 6)
                .fill(Color.black)
                .frame(width: w, height: h)
                .shadow(color: .black.opacity(model.expanded ? 0.45 : 0), radius: 18, y: 8)

            if model.expanded {
                content
                    .padding(.horizontal, 22)
                    .padding(.bottom, 18)
                    .fixedSize(horizontal: false, vertical: true)
                    .background(GeometryReader { g in
                        Color.clear.preference(key: ContentHeightKey.self, value: g.size.height)
                    })
                    .padding(.top, notch.height + 6)
                    .frame(width: Self.width, height: h, alignment: .top)
                    .transition(.asymmetric(insertion: .opacity.combined(with: .scale(scale: 0.92, anchor: .top))
                                                .animation(.spring(response: 0.4, dampingFraction: 0.8).delay(0.05)),
                                            removal: .opacity.animation(.easeOut(duration: 0.12))))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onPreferenceChange(ContentHeightKey.self) { v in
            let target = v + 6
            if v > 0, abs(model.islandContentHeight - target) > 0.5 { model.islandContentHeight = target }
        }
        .animation(.spring(response: 0.42, dampingFraction: 0.78), value: model.expanded)
        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: contentHeight)
        .preferredColorScheme(.dark)
    }

    private var content: some View {
        VStack(spacing: 14) {
            header
            ModePicker(selected: model.nc.mode, enabled: model.ncAvailable) { mode in
                model.requestMode?(mode, nil)
            }
            if model.nc.mode == .ambient && model.ncAvailable {
                AmbientSlider(level: model.nc.level) { lvl in model.requestMode?(.ambient, lvl) }
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
            if model.eq.available && model.ncAvailable {
                EQSection(eq: model.eq,
                          onPreset: { model.requestEQPreset?($0) },
                          onClearBass: { model.requestClearBass?($0) })
                    .transition(.opacity)
            }
            Rectangle().fill(.white.opacity(0.08)).frame(height: 1)
            player
        }
    }

    // MARK: nagłówek: zdjęcie + nazwa + bateria

    private var header: some View {
        HStack(spacing: 14) {
            HeadphoneImage(image: model.image)
            VStack(alignment: .leading, spacing: 4) {
                Text(model.headphones.name)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                statusLine
            }
            Spacer(minLength: 0)
            if let b = model.battery {
                BatteryRing(level: b, charging: model.charging)
            }
        }
    }

    @ViewBuilder private var statusLine: some View {
        if !model.connected {
            Text("Niepołączone").font(.system(size: 12)).foregroundStyle(.white.opacity(0.5))
        } else if model.ncConnecting {
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini).tint(.white)
                Text("Łączenie ze sterowaniem…").font(.system(size: 12)).foregroundStyle(.white.opacity(0.55))
            }
        } else if !model.ncAvailable {
            Text("Sterowanie niedostępne").font(.system(size: 12)).foregroundStyle(.orange.opacity(0.85))
        } else if let m = model.nc.mode {
            Label(m.label, systemImage: m.symbol)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white.opacity(0.6))
        }
    }

    // MARK: odtwarzacz

    private var player: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                if let np = model.nowPlaying {
                    Text(np.title).font(.system(size: 13, weight: .semibold)).foregroundStyle(.white).lineLimit(1)
                    Text(np.artist).font(.system(size: 11.5)).foregroundStyle(.white.opacity(0.55)).lineLimit(1)
                } else {
                    Text("Sterowanie odtwarzaniem").font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(.white.opacity(0.55)).lineLimit(1)
                }
            }
            Spacer(minLength: 4)
            MediaButton(symbol: "backward.fill", size: 34) { model.mediaCommand?(.previous) }
            MediaButton(symbol: (model.nowPlaying?.playing ?? false) ? "pause.fill" : "play.fill",
                        size: 44, primary: true) { model.mediaCommand?(.playPause) }
            MediaButton(symbol: "forward.fill", size: 34) { model.mediaCommand?(.next) }
        }
    }
}

// MARK: - kontrolki

struct ContentHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

/// Equalizer: presety jako przewijane "pigułki" + suwak Clear Bass.
struct EQSection: View {
    let eq: EQState
    let onPreset: (UInt8) -> Void
    let onClearBass: (Int) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "slider.vertical.3").font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.55))
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(eq.presets) { p in
                            PresetChip(name: p.name, selected: eq.preset == p.id) { onPreset(p.id) }
                        }
                    }
                    .padding(.vertical, 1)
                }
            }
            if let cb = eq.clearBass {
                ClearBassSlider(value: cb, onCommit: onClearBass)
            }
        }
    }
}

struct PresetChip: View {
    let name: String
    let selected: Bool
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Text(name)
                .font(.system(size: 11.5, weight: .semibold))
                .lineLimit(1)
                .foregroundStyle(selected ? Color.white : Color.white.opacity(0.72))
                .padding(.horizontal, 11)
                .padding(.vertical, 6)
                .background(Capsule().fill(selected ? Color(red: 0.17, green: 0.58, blue: 1)
                                                    : Color.white.opacity(hover ? 0.16 : 0.09)))
                .contentShape(Capsule())
        }
        .buttonStyle(PressScale())
        .onHover { hover = $0 }
        .animation(.easeOut(duration: 0.15), value: selected)
    }
}

struct ClearBassSlider: View {
    let value: Int
    let onCommit: (Int) -> Void
    @State private var v: Double = 0
    @State private var editing = false

    var body: some View {
        HStack(spacing: 10) {
            Text("Clear Bass").font(.system(size: 11.5, weight: .medium)).foregroundStyle(.white.opacity(0.6))
            Slider(value: $v, in: -10...10, step: 1, onEditingChanged: { e in
                editing = e
                if !e { onCommit(Int(v)) }
            })
            .tint(Color(red: 0.17, green: 0.58, blue: 1))
            Text(Int(v) == 0 ? "0" : String(format: "%+d", Int(v)))
                .font(.system(size: 11, weight: .semibold, design: .rounded))
                .foregroundStyle(.white.opacity(0.75)).frame(width: 26, alignment: .trailing)
        }
        .onAppear { v = Double(value) }
        .onChange(of: value) { new in if !editing { v = Double(new) } }
    }
}

/// Zdjęcie słuchawek: po najechaniu unosi się, powiększa i kołysze.
struct HeadphoneImage: View {
    let image: NSImage?
    @State private var hover = false
    @State private var swing = false
    @State private var pop = false

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
            } else {
                Image(systemName: "headphones").resizable().scaledToFit().foregroundStyle(.white).padding(10)
            }
        }
        .frame(width: 64, height: 64)
        .background(
            Circle()
                .fill(RadialGradient(colors: [.white.opacity(hover ? 0.28 : 0.14), .clear],
                                     center: .center, startRadius: 2, endRadius: 40))
                .frame(width: 84, height: 84)
        )
        .scaleEffect((hover ? 1.12 : 1.0) * (pop ? 1.08 : 1.0))
        .rotationEffect(.degrees(hover ? (swing ? 5 : -5) : 0))
        .offset(y: hover ? (swing ? -4 : -1) : 0)
        .animation(.spring(response: 0.35, dampingFraction: 0.55), value: hover)
        .animation(.spring(response: 0.25, dampingFraction: 0.45), value: pop)
        .contentShape(Rectangle())
        .onHover { h in
            hover = h
            if h {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { swing = true }
            } else {
                withAnimation(.spring()) { swing = false }
            }
        }
        .onTapGesture {
            pop = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) { pop = false }
        }
    }
}

struct BatteryRing: View {
    let level: Int
    let charging: Bool

    private var color: Color { level > 40 ? .green : (level > 15 ? .yellow : .red) }

    var body: some View {
        ZStack {
            Circle().stroke(.white.opacity(0.12), lineWidth: 4)
            Circle()
                .trim(from: 0, to: CGFloat(max(0, min(100, level))) / 100)
                .stroke(color, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(.easeOut(duration: 0.8), value: level)
            VStack(spacing: 0) {
                if charging {
                    Image(systemName: "bolt.fill").font(.system(size: 8)).foregroundStyle(color)
                }
                Text("\(level)%").font(.system(size: 11, weight: .semibold, design: .rounded)).foregroundStyle(.white)
            }
        }
        .frame(width: 44, height: 44)
    }
}

/// Przełącznik trybów w stylu iOS z przesuwaną pigułką.
struct ModePicker: View {
    let selected: SoundMode?
    let enabled: Bool
    let onSelect: (SoundMode) -> Void
    @Namespace private var ns
    @State private var hovered: SoundMode?

    var body: some View {
        HStack(spacing: 0) {
            ForEach(SoundMode.allCases, id: \.self) { m in
                Button { onSelect(m) } label: {
                    HStack(spacing: 5) {
                        Image(systemName: m.symbol).font(.system(size: 11, weight: .semibold))
                        Text(m.label).font(.system(size: 11.5, weight: .semibold)).lineLimit(1).minimumScaleFactor(0.75)
                    }
                    .foregroundStyle(selected == m ? Color.white : Color.white.opacity(enabled ? 0.62 : 0.3))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background {
                        if selected == m {
                            Capsule()
                                .fill(LinearGradient(colors: enabled ? [Color(red: 0.17, green: 0.58, blue: 1), Color(red: 0.04, green: 0.44, blue: 0.88)]
                                                                     : [Color.gray.opacity(0.5), Color.gray.opacity(0.4)],
                                                     startPoint: .top, endPoint: .bottom))
                                .matchedGeometryEffect(id: "pill", in: ns)
                        } else if hovered == m && enabled {
                            Capsule().fill(.white.opacity(0.07))
                        }
                    }
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(!enabled)
                .onHover { h in hovered = h ? m : (hovered == m ? nil : hovered) }
            }
        }
        .padding(3)
        .background(Capsule().fill(.white.opacity(0.09)))
        .animation(.spring(response: 0.32, dampingFraction: 0.8), value: selected)
    }
}

struct AmbientSlider: View {
    let level: Int
    let onCommit: (Int) -> Void
    @State private var value: Double = 10
    @State private var editing = false

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "ear").font(.system(size: 11)).foregroundStyle(.white.opacity(0.55))
            Slider(value: $value, in: 1...20, step: 1, onEditingChanged: { e in
                editing = e
                if !e { onCommit(Int(value)) }
            })
            .tint(Color(red: 0.17, green: 0.58, blue: 1))
            Text("\(Int(value))").font(.system(size: 11, weight: .semibold, design: .rounded))
                .foregroundStyle(.white.opacity(0.7)).frame(width: 20, alignment: .trailing)
        }
        .onAppear { value = Double(level) }
        .onChange(of: level) { new in if !editing { value = Double(new) } }
    }
}

struct MediaButton: View {
    let symbol: String
    let size: CGFloat
    var primary = false
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: size * 0.38, weight: .bold))
                .foregroundStyle(primary ? Color.black : Color.white)
                .frame(width: size, height: size)
                .background(Circle().fill(primary ? Color.white.opacity(hover ? 1 : 0.92)
                                                  : Color.white.opacity(hover ? 0.18 : 0.09)))
                .contentShape(Circle())
        }
        .buttonStyle(PressScale())
        .scaleEffect(hover ? 1.06 : 1)
        .animation(.spring(response: 0.25, dampingFraction: 0.7), value: hover)
        .onHover { hover = $0 }
    }
}

struct PressScale: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.88 : 1)
            .animation(.spring(response: 0.2, dampingFraction: 0.6), value: configuration.isPressed)
    }
}
