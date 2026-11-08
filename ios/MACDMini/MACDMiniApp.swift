import SwiftUI

@main
struct MACDMiniApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView().environment(model)
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        TabView(selection: $model.tab) {
            Tab("Lanes", systemImage: "chart.line.uptrend.xyaxis", value: AppTab.lanes) { LanesView() }
            Tab("MACD book", systemImage: "list.bullet.rectangle", value: AppTab.book) { BookView() }
            Tab("Signals", systemImage: "bolt", value: AppTab.signals) { SignalsView() }
            Tab("Sectors", systemImage: "arrow.uturn.down.circle", value: AppTab.sectors) { SectorCoilView() }
            Tab("Settings", systemImage: "gearshape", value: AppTab.settings) { SettingsView() }
        }
        .onAppear { if !model.hasToken { model.tab = .settings } }
    }
}
