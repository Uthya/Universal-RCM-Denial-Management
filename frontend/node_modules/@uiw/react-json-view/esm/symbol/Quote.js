import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var Quote = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Quot = _useSymbolsStore.Quote,
    Comp = _useSymbolsStore$Quot === void 0 ? {} : _useSymbolsStore$Quot;
  useSymbolsRender(Comp, props, 'Quote');
  return null;
};
Quote.displayName = 'JVR.Quote';