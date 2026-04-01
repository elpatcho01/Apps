import SwiftUI

struct OnboardingView: View {

    @StateObject private var viewModel = OnboardingViewModel()
    @AppStorage(LocalStore.nameKey) private var storedName: String = ""
    @AppStorage(LocalStore.codeKey) private var storedCode: String = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 32) {
                    header

                    VStack(spacing: 20) {
                        nameField
                        codeField
                    }
                    .padding(.horizontal)

                    joinButton

                    if let error = viewModel.errorMessage {
                        Text(error)
                            .foregroundStyle(.red)
                            .font(.footnote)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }

                    instructions
                }
                .padding(.vertical, 40)
            }
            .navigationBarHidden(true)
        }
    }

    // MARK: - Subviews

    private var header: some View {
        VStack(spacing: 12) {
            Image(systemName: "note.text")
                .font(.system(size: 64))
                .foregroundStyle(.accent)

            Text("Shared Notes")
                .font(.largeTitle.bold())

            Text("A private space for you and your partner")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal)
    }

    private var nameField: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Your Name", systemImage: "person")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            TextField("e.g. Alice", text: $viewModel.name)
                .textFieldStyle(.plain)
                .padding()
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
                .autocorrectionDisabled()
                .textInputAutocapitalization(.words)
        }
    }

    private var codeField: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Family Code", systemImage: "key")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            TextField("6-character code", text: $viewModel.familyCode)
                .textFieldStyle(.plain)
                .padding()
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
                .autocorrectionDisabled()
                .textInputAutocapitalization(.characters)
                .onChange(of: viewModel.familyCode) { _, newValue in
                    if newValue.count > 6 {
                        viewModel.familyCode = String(newValue.prefix(6))
                    }
                }
        }
    }

    private var joinButton: some View {
        Button {
            viewModel.join { user in
                storedName = user.name
                storedCode = user.familyCode
            }
        } label: {
            Group {
                if viewModel.isLoading {
                    ProgressView()
                        .tint(.white)
                } else {
                    Text("Get Started")
                        .font(.headline)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 52)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(!viewModel.canJoin || viewModel.isLoading)
        .padding(.horizontal)
    }

    private var instructions: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("How it works")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 4) {
                bulletPoint("Pick any 6-character code (e.g. FAMILY)")
                bulletPoint("Your partner enters the same code on their phone")
                bulletPoint("Notes sync instantly between both devices")
            }
        }
        .padding()
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
        .padding(.horizontal)
    }

    private func bulletPoint(_ text: String) -> some View {
        Label(text, systemImage: "circle.fill")
            .font(.caption)
            .foregroundStyle(.secondary)
            .labelStyle(SmallIconLabelStyle())
    }
}

private struct SmallIconLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .top, spacing: 8) {
            configuration.icon
                .font(.system(size: 5))
                .padding(.top, 5)
            configuration.title
        }
    }
}

#Preview {
    OnboardingView()
}
