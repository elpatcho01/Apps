import Foundation

@MainActor
final class OnboardingViewModel: ObservableObject {

    @Published var name: String = ""
    @Published var familyCode: String = ""
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    private let service = FirestoreService()

    var canJoin: Bool {
        name.trimmingCharacters(in: .whitespaces).count >= 2 &&
        familyCode.trimmingCharacters(in: .whitespaces).count == 6
    }

    func join(onSuccess: @escaping (AppUser) -> Void) {
        let trimmedName = name.trimmingCharacters(in: .whitespaces)
        let trimmedCode = familyCode.trimmingCharacters(in: .whitespaces).uppercased()

        isLoading = true
        errorMessage = nil

        Task {
            do {
                try await service.ensureSpace(familyCode: trimmedCode)
                UserDefaults.standard.set(trimmedName, forKey: LocalStore.nameKey)
                UserDefaults.standard.set(trimmedCode, forKey: LocalStore.codeKey)
                onSuccess(AppUser(name: trimmedName, familyCode: trimmedCode))
            } catch {
                errorMessage = "Could not connect. Check your internet and try again."
            }
            isLoading = false
        }
    }
}
