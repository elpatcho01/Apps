import SwiftUI

struct EmptyStateView: View {
    var body: some View {
        ContentUnavailableView(
            "No Notes Yet",
            systemImage: "note.text",
            description: Text("Tap the pencil icon to add your first shared note.")
        )
    }
}

#Preview {
    EmptyStateView()
}
