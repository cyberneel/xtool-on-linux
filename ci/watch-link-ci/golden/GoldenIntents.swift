// Reference App Intents, written to exercise every construct the Linux generator
// (appintents-gen.py) has to emit. Apple's appintentsmetadataprocessor turns this into a
// golden Metadata.appintents bundle we can diff against, since that tool is macOS-only and
// closed source. Nothing here is app-specific — no user code is uploaded to this public repo.
import AppIntents
import Foundation

enum GoldenTone: String, AppEnum {
    case brief, detailed

    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Tone")
    static var caseDisplayRepresentations: [GoldenTone: DisplayRepresentation] = [
        .brief: "Brief",
        .detailed: "Detailed",
    ]
}

struct GoldenNote: AppEntity {
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Note")
    static var defaultQuery = GoldenNoteQuery()

    var id: String
    var displayRepresentation: DisplayRepresentation { DisplayRepresentation(title: "\(id)") }
}

struct GoldenNoteQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [GoldenNote] {
        identifiers.map(GoldenNote.init(id:))
    }
}

// The important one: a parameterSummary is what the Shortcuts editor renders as an action card,
// and it is the metadata our generator currently emits nothing for.
struct AskGoldenIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Golden"
    static var description = IntentDescription("Ask the golden reference a question.")
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Prompt", description: "What to ask")
    var prompt: String

    @Parameter(title: "Tone", default: .brief)
    var tone: GoldenTone

    @Parameter(title: "Repeat Count", default: 1)
    var repeatCount: Int

    static var parameterSummary: some ParameterSummary {
        Summary("Ask \(\.$prompt) using \(\.$tone)") {
            \.$repeatCount
        }
    }

    func perform() async throws -> some IntentResult & ReturnsValue<String> {
        .result(value: prompt)
    }
}

struct OpenGoldenIntent: AppIntent {
    static var title: LocalizedStringResource = "Open Golden"
    static var openAppWhenRun: Bool = true

    func perform() async throws -> some IntentResult { .result() }
}

struct GoldenShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskGoldenIntent(),
            phrases: ["Ask \(.applicationName)", "Ask \(.applicationName) something"],
            shortTitle: "Ask",
            systemImageName: "sparkles"
        )
        AppShortcut(
            intent: OpenGoldenIntent(),
            phrases: ["Open \(.applicationName)"],
            shortTitle: "Open",
            systemImageName: "app"
        )
    }
}
