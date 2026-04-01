import SwiftUI

struct NoteRowView: View {

    let note: Note
    let currentUserName: String

    private var isMine: Bool { note.authorName == currentUserName }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(note.body)
                .font(.body)
                .lineLimit(3)
                .foregroundStyle(.primary)

            HStack {
                Label(note.authorName, systemImage: isMine ? "person.fill" : "person")
                    .font(.caption)
                    .foregroundStyle(isMine ? .accent : .secondary)

                Spacer()

                Text(note.updatedAt, style: .relative)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

#Preview {
    List {
        NoteRowView(
            note: Note(
                id: "1",
                body: "Pick up milk and eggs from the store",
                authorName: "Alice",
                createdAt: Date(),
                updatedAt: Date()
            ),
            currentUserName: "Alice"
        )
        NoteRowView(
            note: Note(
                id: "2",
                body: "Dinner reservation at 7pm Friday — don't forget!",
                authorName: "Bob",
                createdAt: Date(),
                updatedAt: .init(timeIntervalSinceNow: -3600)
            ),
            currentUserName: "Alice"
        )
    }
}
