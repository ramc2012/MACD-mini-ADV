import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var address = ""
    @State private var token = ""
    @State private var checking = false
    @State private var result: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://localhost:8201", text: $address)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Save address") { model.setBaseURL(address); address = model.baseURL }
                        .disabled(address == model.baseURL)
                } header: {
                    Text("Engine address")
                } footer: {
                    Text("The simulator reaches the Mac's engine at localhost:8201. A real iPhone needs the Mac's address, and the engine port is published on 127.0.0.1 only.")
                }

                Section {
                    if model.hasToken {
                        Label("Token saved in the Keychain", systemImage: "checkmark.seal.fill").foregroundStyle(Color.gain)
                    }
                    SecureField(model.hasToken ? "Paste a new token to replace it" : "Paste MACD_API_TOKEN", text: $token)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Save token") { model.saveToken(token); token = ""; result = nil }
                        .disabled(token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if model.hasToken {
                        Button("Remove token", role: .destructive) { model.clearToken(); result = nil }
                    }
                } header: {
                    Text("API token")
                } footer: {
                    Text("The same token the web app uses (MACD_API_TOKEN in the project's .env). It is kept in this device's Keychain and sent only to the engine address above.")
                }

                Section {
                    Button {
                        Task {
                            checking = true
                            await model.refreshHealth()
                            if let h = model.health, model.healthError == nil {
                                result = "Reached the engine: status \(h.status ?? "unknown")\(h.tokenExpired == true ? ", Fyers token expired" : "")."
                            } else {
                                result = model.healthError ?? "No answer."
                            }
                            checking = false
                        }
                    } label: {
                        HStack { Text("Test connection"); if checking { Spacer(); ProgressView() } }
                    }
                    .disabled(!model.hasToken || checking)
                    if let result { Text(result).font(.callout).foregroundStyle(.secondary) }
                }

                Section("About") {
                    Text("A read-only client for the MACD mini paper-trading engine. It shows lanes, the MACD book, signals and the Sector coil. It never places, changes or closes trades.")
                        .font(.callout).foregroundStyle(.secondary)
                    LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")
                }
            }
            .navigationTitle("Settings")
            .onAppear { address = model.baseURL }
        }
    }
}
