import SwiftUI
import FirebaseCore

@main
struct SharedNotesApp: App {

    init() {
        FirebaseApp.configure()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    @AppStorage(LocalStore.nameKey) private var name: String = ""
    @AppStorage(LocalStore.codeKey) private var familyCode: String = ""

    var body: some View {
        if name.isEmpty || familyCode.isEmpty {
            OnboardingView()
        } else {
            NotesListView(user: AppUser(name: name, familyCode: familyCode))
        }
    }
}
