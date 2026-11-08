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
            Tab("Signals", systemImage: "bolt", value: AppTab.signals) { SignalsView() }
            Tab("Breadth", systemImage: "chart.bar.xaxis", value: AppTab.breadth) { BreadthView() }
            Tab("Brokers", systemImage: "key.horizontal", value: AppTab.brokers) { BrokersView() }
            Tab("Settings", systemImage: "gearshape", value: AppTab.settings) { SettingsView() }
        }
        .onAppear { if !model.hasToken { model.tab = .settings } }
    }
}
