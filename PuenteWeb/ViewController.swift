import UIKit

/// Vista invisible que recibe lo que se escribe con el teclado del iPad
final class KeyCatcher: UIView, UIKeyInput {
    var onText: ((String) -> Void)?
    var onDelete: (() -> Void)?
    var autocorrectionType: UITextAutocorrectionType = .no
    var autocapitalizationType: UITextAutocapitalizationType = .none
    var spellCheckingType: UITextSpellCheckingType = .no
    var keyboardType: UIKeyboardType = .default
    var returnKeyType: UIReturnKeyType = .go

    override var canBecomeFirstResponder: Bool { return true }
    var hasText: Bool { return true }
    func insertText(_ text: String) { onText?(text) }
    func deleteBackward() { onDelete?() }
}

class ViewController: UIViewController, UITextFieldDelegate {

    private let server = FrameServer(port: 7777)
    private let toolbar = UIView()
    private let backBtn = UIButton(type: .system)
    private let fwdBtn = UIButton(type: .system)
    private let reloadBtn = UIButton(type: .system)
    private let modeBtn = UIButton(type: .system)
    private let urlField = UITextField()
    private let pageView = UIView()
    private let imageLayer = CALayer()
    private let statusLabel = UILabel()
    private let keyCatcher = KeyCatcher()

    private let toolbarHeight: CGFloat = 48
    private var keyboardHeight: CGFloat = 0
    private var pageKeyboard = false
    private var mobileMode = false
    private var connected = false
    private var gotFrame = false
    private var lastSize = CGSize.zero
    private var flingTimer: Timer?
    private var flingVX: CGFloat = 0
    private var flingVY: CGFloat = 0
    private var flingPoint = CGPoint.zero

    override var prefersStatusBarHidden: Bool { return true }
    override var shouldAutorotate: Bool { return true }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(white: 0.1, alpha: 1)

        // Barra de arriba
        toolbar.backgroundColor = UIColor(white: 0.18, alpha: 1)
        view.addSubview(toolbar)
        setup(backBtn, "◀", #selector(backTapped))
        setup(fwdBtn, "▶", #selector(fwdTapped))
        setup(reloadBtn, "⟳", #selector(reloadTapped))
        setup(modeBtn, "🖥", #selector(modeTapped))
        urlField.borderStyle = .roundedRect
        urlField.placeholder = "Buscar o escribir la dirección"
        urlField.autocapitalizationType = .none
        urlField.autocorrectionType = .no
        urlField.keyboardType = .URL
        urlField.returnKeyType = .go
        urlField.clearButtonMode = .whileEditing
        urlField.delegate = self
        toolbar.addSubview(urlField)

        // Zona de la página
        pageView.backgroundColor = .black
        pageView.clipsToBounds = true
        view.addSubview(pageView)
        imageLayer.contentsGravity = .resizeAspect
        imageLayer.magnificationFilter = .linear
        pageView.layer.addSublayer(imageLayer)

        statusLabel.numberOfLines = 0
        statusLabel.textAlignment = .center
        statusLabel.textColor = .white
        statusLabel.font = UIFont.systemFont(ofSize: 22)
        pageView.addSubview(statusLabel)
        showWaiting()

        // Gestos sobre la página
        pageView.isMultipleTouchEnabled = true
        pageView.addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapped(_:))))
        let pan = UIPanGestureRecognizer(target: self, action: #selector(panned(_:)))
        pan.maximumNumberOfTouches = 2
        pageView.addGestureRecognizer(pan)

        // Teclado para escribir dentro de la página
        keyCatcher.frame = CGRect(x: 0, y: 0, width: 1, height: 1)
        keyCatcher.onText = { [weak self] t in self?.typed(t) }
        keyCatcher.onDelete = { [weak self] in self?.sendLine("KEY,Backspace") }
        view.addSubview(keyCatcher)
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardChanged(_:)),
                                               name: UIResponder.keyboardWillChangeFrameNotification, object: nil)

        server.onFrame = { [weak self] cg in self?.show(cg) }
        server.onMessage = { [weak self] text in self?.handleMessage(text) }
        server.onState = { [weak self] on in self?.stateChanged(on) }
        server.start()
    }

    private func setup(_ b: UIButton, _ title: String, _ action: Selector) {
        b.setTitle(title, for: .normal)
        b.titleLabel?.font = UIFont.systemFont(ofSize: 22)
        b.tintColor = .white
        b.addTarget(self, action: action, for: .touchUpInside)
        toolbar.addSubview(b)
    }

    // MARK: Diseño
    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        let w = view.bounds.width
        let h = view.bounds.height
        toolbar.frame = CGRect(x: 0, y: 0, width: w, height: toolbarHeight)
        backBtn.frame = CGRect(x: 6, y: 4, width: 44, height: 40)
        fwdBtn.frame = CGRect(x: 52, y: 4, width: 44, height: 40)
        reloadBtn.frame = CGRect(x: 98, y: 4, width: 44, height: 40)
        modeBtn.frame = CGRect(x: w - 52, y: 4, width: 44, height: 40)
        urlField.frame = CGRect(x: 148, y: 8, width: max(100, w - 148 - 58), height: 32)
        let pageH = max(100, h - toolbarHeight - keyboardHeight)
        pageView.frame = CGRect(x: 0, y: toolbarHeight, width: w, height: pageH)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        imageLayer.frame = pageView.bounds
        CATransaction.commit()
        statusLabel.frame = pageView.bounds.insetBy(dx: 20, dy: 20)
        sendSizeIfNeeded()
    }

    private func sendSizeIfNeeded() {
        let s = pageView.bounds.size
        if connected && s != lastSize && s.width > 50 && s.height > 50 {
            lastSize = s
            sendLine("SIZE,\(Int(s.width)),\(Int(s.height)),\(mobileMode ? 1 : 0)")
        }
    }

    @objc private func keyboardChanged(_ n: Notification) {
        guard let f = (n.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? NSValue)?.cgRectValue else { return }
        let overlap = max(0, view.bounds.height - view.convert(f, from: nil).minY)
        keyboardHeight = pageKeyboard ? overlap : 0
        view.setNeedsLayout()
    }

    // MARK: Estado de la conexión
    private func showWaiting() {
        var t = "PuenteWeb\n\nEsperando a la PC…\nConectá el cable USB (tocá «Confiar» si lo pide)\ny abrí puente_web.py en la PC."
        if let ip = wifiIPAddress() {
            t += "\n\nIP de este iPad (para Wi-Fi): " + ip
        }
        statusLabel.text = t
        statusLabel.isHidden = false
    }

    private func stateChanged(_ on: Bool) {
        connected = on
        if on {
            lastSize = .zero
            statusLabel.text = "Conectado ✓\nCargando la página…"
            statusLabel.isHidden = false
            sendSizeIfNeeded()
        } else {
            gotFrame = false
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            imageLayer.contents = nil
            CATransaction.commit()
            showWaiting()
        }
    }

    private func show(_ cg: CGImage) {
        if !gotFrame {
            gotFrame = true
            statusLabel.isHidden = true
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        imageLayer.contents = cg
        CATransaction.commit()
    }

    private func handleMessage(_ text: String) {
        if text.hasPrefix("URL,") {
            if !urlField.isEditing {
                urlField.text = String(text.dropFirst(4))
            }
        } else if text == "KB,1" {
            if !urlField.isEditing {
                pageKeyboard = true
                keyCatcher.becomeFirstResponder()
            }
        } else if text == "KB,0" {
            pageKeyboard = false
            keyCatcher.resignFirstResponder()
            keyboardHeight = 0
            view.setNeedsLayout()
        }
    }

    // MARK: Envío a la PC
    private func sendLine(_ s: String) {
        server.sendText(s + "\n")
    }

    private func enc(_ s: String) -> String {
        return s.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""
    }

    private func norm(_ p: CGPoint) -> String {
        let w = max(1, pageView.bounds.width)
        let h = max(1, pageView.bounds.height)
        return String(format: "%.5f,%.5f", Double(p.x / w), Double(p.y / h))
    }

    private func typed(_ text: String) {
        if text == "\n" {
            sendLine("KEY,Enter")
        } else {
            sendLine("TXT," + enc(text))
        }
    }

    // MARK: Botones
    @objc private func backTapped() { sendLine("BACK") }
    @objc private func fwdTapped() { sendLine("FWD") }
    @objc private func reloadTapped() { sendLine("RELOAD") }

    @objc private func modeTapped() {
        mobileMode.toggle()
        modeBtn.setTitle(mobileMode ? "📱" : "🖥", for: .normal)
        sendLine("MODE,\(mobileMode ? 1 : 0)")
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        let t = textField.text ?? ""
        textField.resignFirstResponder()
        if !t.trimmingCharacters(in: .whitespaces).isEmpty {
            sendLine("NAV," + enc(t))
        }
        return true
    }

    func textFieldDidBeginEditing(_ textField: UITextField) {
        DispatchQueue.main.async { textField.selectAll(nil) }
    }

    // MARK: Gestos en la página
    @objc private func tapped(_ g: UITapGestureRecognizer) {
        stopFling()
        urlField.resignFirstResponder()
        sendLine("C," + norm(g.location(in: pageView)))
    }

    @objc private func panned(_ g: UIPanGestureRecognizer) {
        let loc = g.location(in: pageView)
        switch g.state {
        case .began:
            stopFling()
        case .changed:
            let t = g.translation(in: pageView)
            g.setTranslation(.zero, in: pageView)
            sendScroll(loc, t.x, t.y)
        case .ended:
            let v = g.velocity(in: pageView)
            startFling(loc, v)
        default:
            break
        }
    }

    private func sendScroll(_ loc: CGPoint, _ dx: CGFloat, _ dy: CGFloat) {
        sendLine("S," + norm(loc) + "," + String(format: "%.1f,%.1f", Double(dx), Double(dy)))
    }

    private func startFling(_ p: CGPoint, _ v: CGPoint) {
        stopFling()
        if hypot(v.x, v.y) < 200 { return }
        flingPoint = p
        flingVX = v.x
        flingVY = v.y
        flingTimer = Timer.scheduledTimer(timeInterval: 1.0 / 60.0, target: self,
                                          selector: #selector(flingTick), userInfo: nil, repeats: true)
    }

    @objc private func flingTick() {
        flingVX *= 0.95
        flingVY *= 0.95
        sendScroll(flingPoint, flingVX / 60, flingVY / 60)
        if hypot(flingVX, flingVY) < 25 { stopFling() }
    }

    private func stopFling() {
        flingTimer?.invalidate()
        flingTimer = nil
    }
}
