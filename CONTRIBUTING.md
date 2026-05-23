# Contributing to BlenderMentor

First off, thank you for considering contributing to BlenderMentor! It's people like you who make it a great tool for the Blender community.

## 🌈 How Can I Contribute?

### Reporting Bugs
- Use the [GitHub Issue Tracker](https://github.com/blendermentor/blendermentor/issues).
- Describe the bug in detail and provide steps to reproduce it.
- Include your Blender version and OS details.

### Suggesting Enhancements
- Open a [GitHub Issue](https://github.com/blendermentor/blendermentor/issues) with the tag `enhancement`.
- Explain why the feature would be useful and how it should work.

### Pull Requests
1. Fork the repository.
2. Create a new branch for your feature or bugfix (`git checkout -b feature/awesome-feature`).
3. Commit your changes with clear, descriptive messages.
4. Push to your branch and open a Pull Request.

---

## 🛠 Development Workflow

### Requirements
- **Blender 4.2 or newer** is highly recommended (required for modern Extensions platform testing), but backwards-compatible down to **Blender 4.0+**.
- No external Python packages are allowed (we use only Blender's bundled Python and standard library).

### Code Style
- Follow **PEP 8** as much as possible.
- Use descriptive variable and function names.
- Ensure all new UI elements match Blender's native look and feel.
- **License**: All contributions must be compatible with the **GPL v3** license.

### Testing
- Test your changes directly inside Blender.
- Use the built-in **Developer Mode** tools to verify highlights and scene context logic.
- Ensure your code doesn't break existing features (Chat, Navigation, Highlighting). (Unless you're intentionally improving them, which is cool!)

### Highlight Registry
If you are adding support for new UI highlights:
1. Update `addon/ui/highlight.py`.
2. Verify the target using the **Highlight Tester** in Developer Mode.
3. Update the `GEMINI.md` rulebook if the highlight target format changes.

---

## 📄 License
By contributing to BlenderMentor, you agree that your contributions will be licensed under the **GPL v3** License.
