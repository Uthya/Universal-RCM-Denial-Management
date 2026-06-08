import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Set = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Set = _useTypesStore.Set,
    Comp = _useTypesStore$Set === void 0 ? {} : _useTypesStore$Set;
  useTypesRender(Comp, props, 'Set');
  return null;
};
Set.displayName = 'JVR.Set';